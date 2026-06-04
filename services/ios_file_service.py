"""Device file transfer via go-ios fsync.

Two scopes: the AFC media root (no app), or an app's Documents sandbox
(``app=<bundleId>``, only apps with file sharing enabled). No WDA/tunnel needed.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from services import get_adapter
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


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
        return {"path": path, "app": app or None, "tree": cls._goios().fsync_tree(udid, path, app)}

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
