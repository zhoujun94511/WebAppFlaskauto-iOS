"""Encoded screen transport. This is not a JPEG provider.

``BaseScreenProvider.read_frame`` yields JPEG bytes that WebRTC then
re-encodes. A native HEVC/H.264 transport yields already-encoded access
units and must not be forced through that path.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass
class EncodedPacket:
    codec: str
    keyframe: bool
    data: bytes
    codec_string: Optional[str] = None
    description: Optional[bytes] = None
    reset: bool = False


class BaseScreenTransport(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def start(self) -> None:
        """Open the device stream. Raises AppError if it cannot."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Release the stream. Idempotent."""

    @abc.abstractmethod
    def read_packet(self) -> Optional[EncodedPacket]:
        """Next encoded access unit, or None if none arrived in time."""

    @abc.abstractmethod
    def health(self) -> bool:
        """True while the transport can still deliver packets."""

    def request_keyframe(self) -> None:
        """Ask the device encoder for a fresh IDR. Default is a no-op."""
