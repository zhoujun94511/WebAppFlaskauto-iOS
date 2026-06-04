"""Screen providers: screenshot provider frames + fallback behavior."""

from typing import cast

from ios.screen_provider.wda_screenshot import WdaScreenshotProvider
from ios.wda_controller import WDAController


class FakeController:
    def __init__(self, png=b"\x89PNG\r\n\x1a\nfake", healthy=True, raise_=False):
        self._png = png
        self._healthy = healthy
        self._raise = raise_

    @staticmethod
    def create_session():
        return "sess"

    def health_check(self):
        return self._healthy

    def screenshot(self):
        if self._raise:
            from utils.app_errors import AppError, ErrorCode

            raise AppError(ErrorCode.SCREENSHOT_FAILED, "boom")
        return self._png


def test_screenshot_provider_yields_frame():
    controller = cast(WDAController, cast(object, FakeController()))
    p = WdaScreenshotProvider(controller, fps=30)
    p.start()
    frame = p.read_frame()
    assert frame is not None and len(frame) > 0
    assert p.health() is True
    p.stop()
    assert p.read_frame() is None  # stopped → no frame


def test_screenshot_provider_handles_error_frame():
    controller = cast(WDAController, cast(object, FakeController(raise_=True)))
    p = WdaScreenshotProvider(controller, fps=30)
    p.start()
    assert p.read_frame() is None  # error → None, but provider not crashed
    p.stop()
