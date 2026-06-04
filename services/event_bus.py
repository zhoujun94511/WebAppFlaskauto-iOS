"""Server→client event emitter (decoupled from Flask).

``set_emitter`` is wired to ``socketio.emit`` at app startup. Services call
``emit(event, payload)`` to broadcast lifecycle events (device connected /
disconnected, WDA status) without importing the app or socketio directly.
"""

from __future__ import annotations

from typing import Callable, Optional

from utils.logging_setup import get_logger

_log = get_logger(__name__)


def _noop(*_args, **_kwargs) -> None:
    """Default sink until ``set_emitter`` wires socketio.emit at app startup —
    keeps ``_emit`` non-Optional so callers never need a None guard."""


# Always callable (a no-op until wired), so emit() can call it unconditionally.
_emit: Callable[[str, dict, Optional[str]], None] = _noop


def set_emitter(fn: Callable[[str, dict, Optional[str]], None]) -> None:
    global _emit
    _emit = fn


def emit(event: str, payload: dict, room: Optional[str] = None) -> None:
    try:
        _emit(event, payload, room)
    except (RuntimeError, TypeError, ValueError) as exc:
        _log.warning("event emit %s failed: %s", event, exc)
