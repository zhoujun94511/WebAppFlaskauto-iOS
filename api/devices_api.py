"""Device discovery / connection HTTP API (thin — logic is in services)."""

from __future__ import annotations

from flask import Blueprint, Response, request, stream_with_context

from api import api
from services.ios_device_service import IOSDeviceService

bp = Blueprint("devices", __name__, url_prefix="/api/devices")
_svc = IOSDeviceService()


@bp.get("/<udid>/syslog")
def syslog(udid: str):
    """Live device syslog as Server-Sent Events. Login is enforced by the
    global gate; reservation ownership is checked here (a GET isn't auto-gated).
    """
    from services import auth_service, device_log
    from services import reservation_service as reservations

    try:
        reservations.assert_owner(udid, auth_service.current_user())
    except reservations.ReservationError as exc:
        return Response(f"event: error\ndata: {exc}\n\n",
                        status=403, mimetype="text/event-stream")
    resp = Response(stream_with_context(device_log.sse_stream(udid)),
                    mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"  # disable proxy buffering
    return resp


@bp.get("")
@api
def list_devices():
    rescan = request.args.get("rescan", "1") != "0"
    return {"devices": _svc.list_devices(rescan=rescan)}


@bp.get("/<udid>")
@api
def get_device(udid: str):
    return {"device": _svc.get_device(udid)}


@bp.get("/<udid>/info")
@api
def device_info(udid: str):
    """Rich device info (lockdown values + runtime state) for the QuickInfo card."""
    from services import device_info as info

    return {"info": info.collect(udid)}


@bp.post("/<udid>/connect")
@api
def connect(udid: str):
    return {"device": _svc.connect_device(udid)}


@bp.post("/<udid>/disconnect")
@api
def disconnect(udid: str):
    _svc.disconnect_device(udid)
    return {"udid": udid, "connected": False}


@bp.post("/<udid>/wda/status")
@api
def wda_status(udid: str):
    return {"udid": udid, "wda_running": _svc.check_wda(udid)}
