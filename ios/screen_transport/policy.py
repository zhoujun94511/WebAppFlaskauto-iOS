"""Which screen path to use. HEVC is only offered on iOS 27+.

AN userspace RSD tunnel exists from iOS 17.4, and the display service shows up
once a developer image is mounted. Starting it is a different question: on
iOS 18.6.2, ``startmediastream`` returns code 9021, "Remote control requires
iOS 27.0 or later on this device." Anything older stays on WDA MJPEG.
"""

from __future__ import annotations

from typing import Optional

_HEVC_MIN = (27, 0, 0)


def parse_ios_version(text: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for raw in (text or "").split("."):
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def hevc_supported_ios(version: str) -> bool:
    return parse_ios_version(version) >= _HEVC_MIN


def resolve_screen_provider(
    requested: Optional[str],
    ios_version: str,
    default: str = "mjpeg",
) -> str:
    """Map a client request onto ``hevc`` / ``mjpeg`` / ``screenshot``.

    ``auto`` (and an explicit ``hevc`` on an older phone) becomes MJPEG when
    the device is below iOS 27. Unknown versions do the same.
    """
    name = (requested or default or "mjpeg").strip().lower()
    if name == "auto":
        name = "hevc" if hevc_supported_ios(ios_version) else "mjpeg"
    elif name == "hevc" and not hevc_supported_ios(ios_version):
        name = "mjpeg"
    if name not in ("hevc", "mjpeg", "screenshot"):
        name = "mjpeg"
    return name
