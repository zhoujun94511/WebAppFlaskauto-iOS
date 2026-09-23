"""Device file transfer.

Media root listings come from pymobiledevice3 AFC, one directory at a time.
go-ios ``fsync tree`` on this build prints every nested name at column 0, so a
photo inside ``DCIM/100APPLE`` was requested as ``IMG_0132.PNG`` and AFC
answered "object not found". Pull/push still use go-ios fsync. An app Documents
sandbox (``app=<bundleId>``) stays on go-ios as well.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

from services import get_adapter
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def _child(parent: str, name: str) -> str:
    if parent in ("", "/"):
        return "/" + name
    return parent.rstrip("/") + "/" + name


def media_afc_path(path: str) -> str:
    """Map a panel path (``.`` or ``DCIM/100APPLE``) to an AFC absolute path."""
    raw = (path or ".").strip().replace("\\", "/")
    if raw in (".", "", "/"):
        return "/"
    return "/" + raw.strip("/")


class IOSFileService:
    @staticmethod
    def _goios():
        adapter = get_adapter()
        if not (getattr(adapter, "use_goios", False) and adapter.goios.is_available()):
            raise AppError(ErrorCode.BAD_REQUEST,
                           "file transfer requires go-ios, which is not available")
        return adapter.goios

    @classmethod
    def tree(cls, udid: str, path: str = ".", app: Optional[str] = None) -> dict:
        if app:
            return {"path": path, "app": app, "tree": cls._goios().fsync_tree(udid, path, app)}
        try:
            entries = asyncio.run(cls._list_media(udid, media_afc_path(path)))
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 — AFC errors become a panel message
            raise AppError(ErrorCode.BAD_REQUEST, f"list failed: {exc}", {"path": path}) from exc
        return {"path": path, "app": None, "entries": entries}

    @staticmethod
    async def _list_media(udid: str, remote: str) -> list[dict]:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService

        lockdown = await create_using_usbmux(serial=udid)
        async with AfcService(lockdown) as afc:
            names = [name for name in await afc.listdir(remote) if name not in (".", "..")]
            # One AFC operation at a time. This connection is not safe to fan out.
            flags = []
            for name in names:
                flags.append(await afc.isdir(_child(remote, name)))
        entries = []
        for name, is_dir in zip(names, flags):
            rel = media_afc_path(_child(remote, name)).lstrip("/")
            entries.append({"name": name, "path": rel, "isDir": bool(is_dir)})
        entries.sort(key=lambda item: (not item["isDir"], item["name"].lower()))
        return entries

    @classmethod
    def pull_to_temp(cls, udid: str, src_path: str, app: Optional[str] = None) -> str:
        """Pull a device file into a fresh temp DIR and return the local file
        path. go-ios treats ``--dstPath`` as a directory and writes
        ``<dir>/<basename(src)>`` inside it. Caller streams the file then removes
        the parent dir."""
        import shutil

        tmp_dir = tempfile.mkdtemp(prefix="iosfile_")
        name = os.path.basename(src_path.rstrip("/")) or "download"
        ok, msg = cls._goios().fsync_pull(udid, src_path, tmp_dir, app)
        local = os.path.join(tmp_dir, name)
        if not ok or not os.path.exists(local):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise AppError(ErrorCode.BAD_REQUEST, f"pull failed: {msg[:200]}", {"src": src_path})
        return local

    @classmethod
    def push(cls, udid: str, local_path: str, dst_path: str, app: Optional[str] = None) -> dict:
        ok, msg = cls._goios().fsync_push(udid, local_path, dst_path, app)
        if not ok:
            raise AppError(ErrorCode.BAD_REQUEST, f"push failed: {msg[:200]}", {"dst": dst_path})
        _log.info("pushed file to %s on %s", dst_path, udid[:12])
        return {"dst_path": dst_path, "pushed": True}
