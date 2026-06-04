"""Log Socket.IO events: subscribe/unsubscribe + initial backlog.

Live ``log:entry`` events are broadcast globally from
``register_handlers`` (the logger subscription). Here we just send the recent
backlog when a client subscribes so the LogPanel isn't empty on open.
"""

from __future__ import annotations

from utils.logging_setup import recent_logs


def register(socketio) -> None:
    @socketio.on("logs:subscribe")
    def _subscribe(_data=None):
        level = (_data or {}).get("level")
        for entry in recent_logs(limit=200, level=level):
            socketio.emit("log:entry", entry)

    @socketio.on("logs:unsubscribe")
    def _unsubscribe(_data=None):
        # Live broadcast is global in phase 1; nothing per-client to tear down.
        return
