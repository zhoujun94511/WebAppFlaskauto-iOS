"""Stream Socket.IO events: join the per-udid room, start/stop/status.

Frames are pushed by StreamSession to ``room=udid``; a client must join that
room (done here on stream:start) to receive ``stream:frame`` events.
"""

from __future__ import annotations

from contextlib import suppress
from typing import cast

from flask import request
from flask_socketio import join_room, leave_room

from services import get_adapter
from services.ios_stream_service import IOSStreamService
from services.runtime_state import state
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)

_svc: IOSStreamService | None = None


def _sid() -> str:
    return cast(str, getattr(request, "sid"))


def _service() -> IOSStreamService:
    global _svc
    if _svc is None:
        _svc = IOSStreamService(get_adapter().config)
    return _svc


def register(socketio) -> None:
    @socketio.on("stream:start")
    def _start(data):
        udid = (data or {}).get("udid")
        if not udid:
            socketio.emit("stream:error", {"code": "BAD_REQUEST", "message": "udid required"})
            return
        from socketio_handlers import reservation_gate

        denied = reservation_gate(udid)
        if denied:
            socketio.emit("stream:error", {"code": "RESERVATION_DENIED", "message": denied}, room=_sid())
            return
        join_room(udid)
        state.add_viewer(udid, _sid())  # this client is now watching
        state.touch(udid)
        try:
            status = _service().start_stream(
                udid,
                provider=(data or {}).get("provider"),
                fps=(data or {}).get("fps"),
            )
            socketio.emit("stream:status", status, room=udid)
        except AppError as exc:
            socketio.emit("stream:error", exc.to_dict(), room=udid)
        except Exception as exc:  # noqa: BLE001 — never let a handler crash the worker thread
            _log.exception("stream:start failed unexpectedly for %s", udid)
            socketio.emit(
                "stream:error",
                {"code": ErrorCode.INTERNAL_ERROR, "message": str(exc)},
                room=udid,
            )

    @socketio.on("stream:stop")
    def _stop(data):
        udid = (data or {}).get("udid")
        if not udid:
            return
        state.remove_viewer(udid, _sid())
        _service().stop_stream(udid)
        leave_room(udid)
        socketio.emit("stream:stopped", {"udid": udid}, room=udid)

    @socketio.on("stream:status")
    def _status(data):
        udid = (data or {}).get("udid")
        if not udid:
            return
        socketio.emit("stream:status", _service().get_status(udid), room=udid)

    # NOTE: the "disconnect" handler lives in socketio_handlers/connection.py
    # (Flask-SocketIO allows only one handler per event) and calls
    # stop_orphaned_streams() below for the stream half of the cleanup.


def stop_orphaned_streams(emptied_udids) -> None:
    """Stop streams whose last viewer just disconnected (called from the central
    disconnect handler)."""
    for udid in emptied_udids:
        _log.info("client gone, no viewers left for %s — stopping stream", udid)
        with suppress(Exception):
            _service().stop_stream(udid)
