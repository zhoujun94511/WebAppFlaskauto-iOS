"""Pluggable screen-frame source interface.

A provider yields JPEG frame bytes. The StreamBridge pulls frames (via
``frames()`` generator or repeated ``read_frame()``) and ships them to the
browser. Swapping MJPEG ↔ screenshot is just a different provider — nothing
upstream changes.
"""

from __future__ import annotations

import abc
from typing import Iterator, Optional, Tuple


class BaseScreenProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def start(self) -> None:
        """Acquire resources (open MJPEG socket, warm up WDA session, ...)."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Release all resources. Must be idempotent."""

    @abc.abstractmethod
    def read_frame(self) -> Optional[bytes]:
        """Return the next JPEG frame, or None if temporarily unavailable."""

    def frames(self) -> Iterator[bytes]:
        """Default generator built on ``read_frame``; providers may override."""
        while True:
            frame = self.read_frame()
            if frame is None:
                break
            yield frame

    @abc.abstractmethod
    def health(self) -> bool:
        """True if the provider is currently able to deliver frames."""

    def get_frame_size(self) -> Optional[Tuple[int, int]]:
        """(width, height) of the frames in device pixels, if known."""
        return None
