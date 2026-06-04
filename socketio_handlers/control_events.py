"""Control Socket.IO events (optional alt path to the HTTP control API).

SECURITY: these mutate the device, so they go through the SAME gate as every
other control surface — login + reservation ownership (admins/super_admins
bypass, a normal user must hold the device's reservation). Without this, any
connected socket could drive any device, bypassing the HTTP before_request
gate entirely (the HTTP API enforces it, so this realtime path must too).
"""

from __future__ import annotations

from services.ios_control_service import IOSControlService
from utils.app_errors import AppError, ErrorCode

_svc = IOSControlService()


def register(socketio) -> None:
    @socketio.on("control:tap")
    def _tap(data):
        _run(socketio, "control:tap", data, lambda: _svc.tap(data["udid"], data))

    @socketio.on("control:swipe")
    def _swipe(data):
        _run(socketio, "control:swipe", data, lambda: _svc.swipe(data["udid"], data))

    @socketio.on("control:input")
    def _input(data):
        _run(socketio, "control:input", data, lambda: _svc.input_text(data["udid"], data))


def _gate(data) -> None:
    """Raise AppError unless the socket's user may control ``data['udid']``."""
    from socketio_handlers import reservation_gate

    udid = (data or {}).get("udid")
    if not udid:
        raise AppError(ErrorCode.BAD_REQUEST, "udid required")
    denied = reservation_gate(udid)  # login + reservation ownership (admin bypass)
    if denied:
        raise AppError(ErrorCode.RESERVATION_DENIED, denied)


def _run(socketio, event, data, fn):
    try:
        _gate(data)
        result = fn()
        socketio.emit(f"{event}:ok", {"success": True, "data": result})
    except AppError as exc:
        socketio.emit("stream:error", exc.to_dict())
    except (KeyError, TypeError, ValueError) as exc:
        socketio.emit(
            "stream:error",
            {"success": False, "code": "INTERNAL_ERROR", "message": str(exc)},
        )
