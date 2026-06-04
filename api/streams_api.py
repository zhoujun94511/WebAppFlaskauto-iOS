"""Stream HTTP API: start / stop / status."""

from __future__ import annotations

from flask import Blueprint, request

from api import api
from services import get_adapter
from services.ios_stream_service import IOSStreamService

bp = Blueprint("streams", __name__, url_prefix="/api/devices")

_svc: IOSStreamService | None = None


def _service() -> IOSStreamService:
    global _svc
    if _svc is None:
        _svc = IOSStreamService(get_adapter().config)
    return _svc


@bp.post("/<udid>/stream/start")
@api
def start(udid: str):
    body = request.get_json(silent=True) or {}
    return _service().start_stream(
        udid, provider=body.get("provider"), fps=body.get("fps")
    )


@bp.post("/<udid>/stream/stop")
@api
def stop(udid: str):
    _service().stop_stream(udid)
    return {"udid": udid, "running": False}


@bp.get("/<udid>/stream/status")
@api
def status(udid: str):
    return _service().get_status(udid)


@bp.post("/<udid>/stream/quality")
@api
def quality(udid: str):
    """Live-set the MJPEG downscale % (grid view drops to ~70 for CPU, single
    restores 100). Best-effort — needs WDA up."""
    body = request.get_json(silent=True) or {}
    return get_adapter().set_mjpeg_scaling(udid, int(body.get("scaling", 100)))
