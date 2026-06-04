"""Fallback provider: poll WDA /screenshot at a fixed FPS.

Simple and robust — works on any device where WDA is up. Lower fps than
MJPEG but never gets stuck on a half-parsed multipart stream.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from ios.screen_provider.base_provider import BaseScreenProvider
from ios.wda_controller import WDAController
from utils.app_errors import AppError
from utils.image_utils import image_size, to_jpeg
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class WdaScreenshotProvider(BaseScreenProvider):
    name = "screenshot"

    def __init__(self, controller: WDAController, fps: int = 8, jpeg_quality: int = 70) -> None:
        self.controller = controller
        self.fps = max(1, int(fps))
        self.jpeg_quality = jpeg_quality
        self._running = False
        self._size: Optional[Tuple[int, int]] = None
        self._min_interval = 1.0 / self.fps
        self._last = 0.0

    def start(self) -> None:
        self.controller.create_session()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def read_frame(self) -> Optional[bytes]:
        if not self._running:
            return None
        # Pace to the configured FPS.
        wait = self._min_interval - (time.perf_counter() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.perf_counter()
        try:
            png = self.controller.screenshot()
        except AppError as exc:
            _log.warning("screenshot provider frame failed: %s", exc.message)
            return None
        if self._size is None:
            self._size = image_size(png)
        return to_jpeg(png, quality=self.jpeg_quality)

    def health(self) -> bool:
        return self._running and self.controller.health_check()

    def get_frame_size(self) -> Optional[Tuple[int, int]]:
        return self._size
