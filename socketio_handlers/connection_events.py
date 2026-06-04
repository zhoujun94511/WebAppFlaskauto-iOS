"""Central connection lifecycle: the single Socket.IO ``disconnect`` handler.

Flask-SocketIO allows only one handler per event, so both the stream and
WebRTC cleanups run here when a client (browser tab) goes away:
  * close any WebRTC peer connections for that client;
  * drop it from stream viewer sets and stop streams with no viewers left.
The device stays connected (WDA up) until the idle reaper releases it.
"""

from __future__ import annotations

from contextlib import suppress
from typing import cast

from flask import request

from services import get_adapter
from services.runtime_state import state
from socketio_handlers.streams_events import stop_orphaned_streams
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def register(socketio) -> None:
    @socketio.on("disconnect")
    def _disconnect():
        sid = cast(str, getattr(request, "sid"))
        with suppress(Exception):
            get_adapter().webrtc.stop_sid(sid)
        emptied = []
        with suppress(Exception):
            emptied = state.drop_sid(sid)
        stop_orphaned_streams(emptied)
