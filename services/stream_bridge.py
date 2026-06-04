"""Socket.IO frame bridge (phase-1 transport).

A StreamSession runs one background thread per device: it pulls JPEG frames
from a ScreenProvider and emits them to the Socket.IO room named by udid.
The emit function is injected at app startup (``set_emitter``) so this module
doesn't import the Flask app (keeps it unit-testable and lets WebRTC replace
the transport later without touching the provider/session logic).

NOTE: the seam for WebRTC is deliberate — swap ``_emit`` for a
WebRTCBridge.push_frame(udid, frame) and the rest is unchanged.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from typing import Callable, Optional

from ios.screen_provider.base_provider import BaseScreenProvider
from utils.image_utils import to_data_url
from utils.logging_setup import get_logger

_log = get_logger(__name__)

# (event, payload, room) -> None ; wired to socketio.emit in app.py
def _noop_emit(*_args, **_kwargs) -> None:
    """Default sink until ``set_emitter`` wires socketio.emit — keeps ``_emit``
    non-Optional so ``_emit_safe`` can call it without a None guard."""


_emit: Callable[[str, dict, Optional[str]], None] = _noop_emit


def set_emitter(fn: Callable[[str, dict, Optional[str]], None]) -> None:
    global _emit
    _emit = fn


def _emit_safe(event: str, payload: dict, room: Optional[str]) -> None:
    with suppress(Exception):
        _emit(event, payload, room)


class StreamSession:
    def __init__(
        self,
        udid: str,
        provider: BaseScreenProvider,
        max_fps: int = 15,
        on_unhealthy: Optional[Callable[[str], None]] = None,
    ):
        self.udid = udid
        self.provider = provider
        self.max_fps = max(1, max_fps)
        self.on_unhealthy = on_unhealthy
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seq = 0
        self.provider_name = getattr(provider, "name", "unknown")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.provider.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"stream-{self.udid}", daemon=True)
        self._thread.start()
        _emit_safe("stream:started", {"udid": self.udid, "provider": self.provider_name}, self.udid)

    def _run(self) -> None:
        min_interval = 1.0 / self.max_fps
        last = 0.0
        misses = 0
        while not self._stop.is_set():
            frame = self.provider.read_frame()
            # If we were stopped while blocked in read_frame (stop() closes the
            # provider socket out from under the recv), that's a CLEAN shutdown
            # — not an unhealthy provider. Exit without emitting stream:error or
            # triggering auto-recovery (which would resurrect a stopped stream).
            if self._stop.is_set():
                break
            if frame is None:
                misses += 1
                if misses >= 30 or not self.provider.health():
                    _log.warning("stream %s provider unhealthy, stopping", self.udid)
                    _emit_safe(
                        "stream:error",
                        {"udid": self.udid, "code": "SCREEN_PROVIDER_FAILED",
                         "message": "Screen provider stopped delivering frames"},
                        self.udid,
                    )
                    if self.on_unhealthy:
                        self.on_unhealthy(self.udid)
                    break
                time.sleep(0.1)
                continue
            misses = 0
            # Pace output.
            wait = min_interval - (time.perf_counter() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.perf_counter()
            self._seq += 1
            size = self.provider.get_frame_size()
            _emit_safe(
                "stream:frame",
                {
                    "udid": self.udid,
                    "seq": self._seq,
                    "image": to_data_url(frame),
                    "width": size[0] if size else None,
                    "height": size[1] if size else None,
                },
                self.udid,
            )

    def stop(self) -> None:
        self._stop.set()
        with suppress(Exception):
            self.provider.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        _emit_safe("stream:stopped", {"udid": self.udid}, self.udid)

    def status(self) -> dict:
        alive = bool(self._thread and self._thread.is_alive())
        return {
            "udid": self.udid,
            "running": alive,
            "provider": self.provider_name,
            "frames": self._seq,
            "healthy": self.provider.health() if alive else False,
        }
