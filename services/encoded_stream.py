"""Ship encoded HEVC access units to the browser. No JPEG, no re-encode.

The Socket.IO payload matches the WebCodecs path: one ``stream:hevc-config``
(codec string + base64 hvcC) and then ``stream:hevc`` frames whose ``packet``
bytes are ``frame_access_unit`` output.
"""

from __future__ import annotations

import base64
import threading
from contextlib import suppress
from typing import Callable, Optional

from ios.screen_transport.base import BaseScreenTransport
from services.stream_bridge import _emit_safe
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class EncodedStreamSession:
    def __init__(
        self,
        udid: str,
        transport: BaseScreenTransport,
        on_unhealthy: Optional[Callable[[str], None]] = None,
    ):
        self.udid = udid
        self.transport = transport
        self.on_unhealthy = on_unhealthy
        self.provider_name = getattr(transport, "name", "hevc")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._frames = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.transport.start()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"hevc-bridge-{self.udid[:8]}", daemon=True
        )
        self._thread.start()
        _emit_safe(
            "stream:started",
            {"udid": self.udid, "provider": self.provider_name},
            self.udid,
        )

    def _run(self) -> None:
        misses = 0
        config_sent = False
        while not self._stop.is_set():
            packet = self.transport.read_packet()
            if self._stop.is_set():
                break
            if packet is None:
                misses += 1
                if misses >= 30 or not self.transport.health():
                    _log.warning("hevc stream %s unhealthy, stopping", self.udid[:8])
                    # Close the tunnel before recovery. ``_on_unhealthy`` also
                    # releases, and a second ``stop`` on the transport is safe.
                    self.release()
                    _emit_safe(
                        "stream:error",
                        {
                            "udid": self.udid,
                            "code": "SCREEN_PROVIDER_FAILED",
                            "message": "HEVC transport stopped delivering frames",
                        },
                        self.udid,
                    )
                    if self.on_unhealthy:
                        self.on_unhealthy(self.udid)
                    break
                continue
            misses = 0
            if (
                not config_sent
                and packet.codec_string
                and packet.description
            ):
                _emit_safe(
                    "stream:hevc-config",
                    {
                        "udid": self.udid,
                        "codec": packet.codec_string,
                        "description": base64.b64encode(packet.description).decode("ascii"),
                    },
                    self.udid,
                )
                config_sent = True
            if not config_sent:
                continue
            self._frames += 1
            _emit_safe(
                "stream:hevc",
                {"udid": self.udid, "packet": packet.data},
                self.udid,
            )

    def request_keyframe(self) -> None:
        with suppress(Exception):
            self.transport.request_keyframe()

    def release(self) -> None:
        """Close the device tunnel without joining this session thread.

        The unhealthy path runs on this thread. Joining it here would deadlock.
        ``stop`` still joins when a different thread asks the session to end.
        """
        self._stop.set()
        with suppress(Exception):
            self.transport.stop()

    def stop(self) -> None:
        self.release()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def status(self) -> dict:
        alive = self._thread is not None and self._thread.is_alive()
        return {
            "udid": self.udid,
            "running": alive,
            "provider": self.provider_name,
            "frames": self._frames,
            "healthy": self.transport.health() if alive else False,
        }
