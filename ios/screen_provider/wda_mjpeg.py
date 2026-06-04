"""Primary provider: consume WDA's MJPEG stream (multipart/x-mixed-replace).

WDA exposes an MJPEG server (default device port 9100, forwarded locally).
We stream the multipart body and split on JPEG SOI/EOI markers. On any read
error we mark unhealthy so the StreamService can fall back to screenshots.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Optional, Tuple

import httpx

from ios.screen_provider.base_provider import BaseScreenProvider
from utils.app_errors import AppError, ErrorCode
from utils.image_utils import image_size
from utils.logging_setup import get_logger

_log = get_logger(__name__)

_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


class WdaMjpegProvider(BaseScreenProvider):
    name = "mjpeg"

    def __init__(self, mjpeg_url: str, timeout: float = 10.0):
        self.mjpeg_url = mjpeg_url
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._resp = None
        self._iter = None
        self._buf = bytearray()
        self._running = False
        self._healthy = False
        self._stopping = False  # set by stop() so the read-race isn't an "error"
        self._size: Optional[Tuple[int, int]] = None
        self._frames = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        import time

        with self._lock:
            if self._running:
                return
            self._stopping = False
            last_exc = None
            # WDA's MJPEG server can reset the first connection (especially
            # right after a settings change / under reconnect churn). Retry a
            # few times AND validate by pulling the first chunk, so a
            # reset-on-open is detected here (→ clean screenshot fallback)
            # rather than surfacing as a dead "running" stream.
            for attempt in range(3):
                client = resp = None
                try:
                    client = httpx.Client(timeout=httpx.Timeout(self.timeout, read=15.0))
                    # Low-level streaming: send(stream=True) keeps the response
                    # open until we explicitly close it (the .stream() context
                    # manager would close the body as soon as it's GC'd).
                    req = client.build_request("GET", self.mjpeg_url)
                    resp = client.send(req, stream=True)
                    resp.raise_for_status()
                    it = resp.iter_bytes()
                    first = next(it)  # confirm the stream actually delivers bytes
                    self._client, self._resp, self._iter = client, resp, it
                    if first:
                        self._buf.extend(first)
                    self._running = True
                    self._healthy = True
                    return
                except (httpx.HTTPError, OSError, ValueError, StopIteration) as exc:
                    last_exc = exc
                    with suppress(Exception):
                        if resp is not None:
                            resp.close()
                    with suppress(Exception):
                        if client is not None:
                            client.close()
                    time.sleep(0.5)
            self._cleanup()
            raise AppError(
                ErrorCode.MJPEG_STREAM_FAILED,
                "Could not open WDA MJPEG stream",
                {"url": self.mjpeg_url, "error": str(last_exc)},
            )

    def read_frame(self) -> Optional[bytes]:
        if not self._running or self._iter is None:
            return None
        try:
            while True:
                # Return the NEWEST complete JPEG in the buffer and drop any
                # older queued frames. When the consumer (JPEG decode → H264
                # re-encode) briefly falls behind during fast motion (swipes),
                # serving the freshest frame keeps latency flat instead of
                # replaying a stale backlog.
                end = self._buf.rfind(_JPEG_EOI)
                start = self._buf.rfind(_JPEG_SOI, 0, end) if end != -1 else -1
                if start != -1 and end != -1:
                    frame = bytes(self._buf[start : end + 2])
                    del self._buf[: end + 2]  # consume this frame + discard stale ones before it
                    if self._size is None:
                        self._size = image_size(frame)
                    self._frames += 1
                    return frame
                chunk = next(self._iter)
                if not chunk:
                    self._healthy = False
                    return None
                self._buf.extend(chunk)
                # Guard against unbounded growth on a malformed stream.
                if len(self._buf) > 8 * 1024 * 1024:
                    self._buf.clear()
                    self._healthy = False
                    return None
        except StopIteration:
            self._healthy = False
            return None
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            self._healthy = False
            if self._stopping:
                # We closed the socket on purpose (stop()/device switch); the
                # in-flight recv failing is expected, not a real fault.
                _log.debug("MJPEG read aborted by stop: %s", type(exc).__name__)
                return None
            # Genuine failure: log type + repr + traceback so the root cause is
            # visible (ReadTimeout vs ConnectionReset vs closed socket), not an
            # opaque "[WinError 10038]".
            _log.warning(
                "MJPEG read error after %d frame(s): %s: %r",
                self._frames, type(exc).__name__, exc, exc_info=True,
            )
            return None

    def stop(self) -> None:
        self._stopping = True  # tell the reader the upcoming close is intentional
        with self._lock:
            self._cleanup()

    def _cleanup(self) -> None:
        self._running = False
        self._healthy = False
        with suppress(Exception):
            if self._resp is not None:
                self._resp.close()
        with suppress(Exception):
            if self._client is not None:
                self._client.close()
        self._resp = None
        self._client = None
        self._iter = None
        self._buf = bytearray()

    def health(self) -> bool:
        return self._running and self._healthy

    def get_frame_size(self) -> Optional[Tuple[int, int]]:
        return self._size
