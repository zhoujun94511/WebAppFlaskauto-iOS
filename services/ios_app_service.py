"""App management: list / install / uninstall installed apps.

Backed by go-ios (usbmux/installation-proxy) — independent of WDA and the RSD
tunnel, so it works as soon as the device is paired.
"""

from __future__ import annotations

from services import get_adapter
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class IOSAppService:
    @staticmethod
    def _goios():
        adapter = get_adapter()
        if not (getattr(adapter, "use_goios", False) and adapter.goios.is_available()):
            raise AppError(ErrorCode.BAD_REQUEST,
                           "app management requires go-ios, which is not available")
        return adapter.goios

    @classmethod
    def list_apps(cls, udid: str, system: bool = False) -> dict:
        return {"apps": cls._goios().list_apps(udid, system=system)}

    @classmethod
    def uninstall(cls, udid: str, bundle_id: str) -> dict:
        ok, msg = cls._goios().uninstall_app(udid, bundle_id)
        if not ok:
            raise AppError(ErrorCode.BAD_REQUEST, f"uninstall failed: {msg[:200]}",
                           {"bundle_id": bundle_id})
        _log.info("uninstalled %s from %s", bundle_id, udid[:12])
        return {"bundle_id": bundle_id, "uninstalled": True}

    @classmethod
    def install(cls, udid: str, ipa_path: str) -> dict:
        ok, msg = cls._goios().install_app(udid, ipa_path)
        if not ok:
            raise AppError(ErrorCode.BAD_REQUEST, f"install failed: {msg[:300]}")
        _log.info("installed ipa on %s", udid[:12])
        return {"installed": True, "detail": msg[:200]}
