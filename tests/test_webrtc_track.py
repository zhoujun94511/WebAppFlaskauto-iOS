"""WebRTC JPEG track: keep MJPEG draining, and reopen it before screenshots."""

import asyncio
import io
import logging
import threading
import time

from PIL import Image

from ios.screen_provider.base_provider import BaseScreenProvider
from ios.screen_provider.wda_mjpeg import WdaMjpegProvider
from services.webrtc_bridge import JpegVideoTrack


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


JPEG = _jpeg()


class _Provider(BaseScreenProvider):
    def __init__(self, name: str, frames):
        self.name = name
        self._frames = list(frames)
        self.stopped = False
        self.reads = 0
        self._stop = threading.Event()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True
        self._stop.set()

    def health(self) -> bool:
        return not self.stopped and bool(self._frames)

    def read_frame(self) -> bytes | None:
        self.reads += 1
        if self._stop.is_set() or not self._frames:
            return None
        item = self._frames.pop(0)
        return item if isinstance(item, bytes) else None


class _Hold(BaseScreenProvider):
    """Healthy MJPEG that keeps producing until stopped. Counts reads."""

    name = "mjpeg"

    def __init__(self):
        self.reads = 0
        self._stop = threading.Event()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self._stop.set()

    def health(self) -> bool:
        return not self._stop.is_set()

    def read_frame(self) -> bytes | None:
        self.reads += 1
        self._stop.wait(0.05)
        if self._stop.is_set():
            return None
        return JPEG


def test_pump_drains_mjpeg_before_recv():
    provider = _Hold()
    track = JpegVideoTrack(lambda _recover: provider, initial=provider)
    try:
        time.sleep(0.35)
        assert provider.reads >= 3
    finally:
        track.stop()


def test_stream_end_reopens_mjpeg_before_screenshot():
    created: list[BaseScreenProvider] = []

    def factory(recover: bool):
        if recover:
            provider = _Hold()
            provider.name = "screenshot"
        else:
            provider = _Provider("mjpeg", [])
        created.append(provider)
        return provider

    initial = _Provider("mjpeg", [])
    track = JpegVideoTrack(factory, initial=initial)
    try:
        frame = asyncio.run(asyncio.wait_for(track.recv(), timeout=4))
    finally:
        track.stop()
    assert frame.width == 2 and frame.height == 2
    mjpeg_reopens = [item for item in created if item.name == "mjpeg"]
    shots = [item for item in created if item.name == "screenshot"]
    assert len(mjpeg_reopens) == 3
    assert len(shots) == 1
    assert all(isinstance(item, _Provider) and item.stopped for item in mjpeg_reopens)


def test_screenshot_mode_can_return_to_mjpeg():
    shot = _Hold()
    shot.name = "screenshot"
    nxt = _Hold()
    calls: list[bool] = []

    def factory(recover: bool):
        calls.append(recover)
        return nxt if not recover else shot

    track = JpegVideoTrack(factory, initial=shot)
    try:
        track._using_screenshot = True
        track._attempt_mjpeg_return()
        assert calls == [False]
        assert track._provider is nxt
        assert track._using_screenshot is False
    finally:
        track.stop()


def test_mjpeg_end_logs_frame_count_and_reason(caplog):
    provider = WdaMjpegProvider("http://127.0.0.1:9")
    provider._running = True
    provider._iter = iter(())
    provider._frames = 4
    with caplog.at_level(logging.WARNING):
        assert provider.read_frame() is None
    assert "MJPEG stream ended after 4 frame(s): server closed the stream" in caplog.text
