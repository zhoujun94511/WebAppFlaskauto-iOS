"""Health + environment check endpoint."""

from __future__ import annotations

import platform
import sys

from flask import Blueprint

from api import api
from services import get_adapter

bp = Blueprint("health", __name__, url_prefix="/api")


def _hevc_available() -> bool:
    try:
        from ios.screen_transport.hevc_rsd import hevc_api_available

        return hevc_api_available()
    except ImportError:
        return False


@bp.get("/health")
@api
def health():
    adapter = get_adapter()
    return {
        "status": "ok",
        "platform": "ios",
        "host_os": platform.system(),
        "python": sys.version.split()[0],
        "pymobiledevice3_available": adapter.is_backend_available(),
        "webrtc_enabled": bool(getattr(adapter, "webrtc", None) and adapter.webrtc.enabled),
        "hevc_available": _hevc_available(),
    }
