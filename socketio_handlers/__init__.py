"""Socket.IO event registration + cross-cutting wiring.

``register_handlers(socketio)`` attaches all event handlers and wires the
stream bridge's frame emitter to ``socketio.emit`` (room = udid). The device
syslog is served separately over HTTP SSE (see api/devices_api.py), not here.
"""

from __future__ import annotations

from typing import Optional

from services.stream_bridge import set_emitter
from services.event_bus import set_emitter as set_event_emitter
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def reservation_gate(udid: str) -> Optional[str]:
    """Socket-plane reservation check. Returns an error message to deny, or None
    to allow."""
    from services import auth_service, reservation_service as reservations

    try:
        reservations.assert_owner(udid, auth_service.user_from_socket())
        return None
    except reservations.ReservationError as exc:
        return str(exc)


def register_handlers(socketio) -> None:
    # Frame/event emitter for the stream bridge (background-thread safe under
    # the threading async_mode).
    def _emit(event: str, payload: dict, room: Optional[str]) -> None:
        socketio.emit(event, payload, room=room)

    set_emitter(_emit)
    set_event_emitter(_emit)

    from socketio_handlers import devices_events as devices, control_events as control, streams_events as streams, webrtc_events as webrtc, connection_events as connection

    devices.register(socketio)
    control.register(socketio)
    streams.register(socketio)
    webrtc.register(socketio)
    connection.register(socketio)  # single disconnect handler (last)
    _log.info("Socket.IO handlers registered")
