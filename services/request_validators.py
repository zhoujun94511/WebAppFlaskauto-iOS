"""Request validation + display→device coordinate mapping.

The coordinate mapper is the heart of "click on the browser image → tap on
the phone". The browser sends where it was clicked (display_x/y) and how big
the rendered image was (display_width/height); we map that into device pixel
space, accounting for aspect-ratio letterboxing (the contain-fit black bars)
and orientation.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from utils.app_errors import AppError, ErrorCode

# ── user field validation (mirrors the Android sibling) ──────────────
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_username(name: str) -> str:
    return (name or "").strip()


def validate_username(name: str) -> Optional[str]:
    if not _USERNAME_RE.match(name or ""):
        return "用户名需为3-32位，以字母或数字开头，仅含字母、数字、下划线、点或连字符"
    return None


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> Optional[str]:
    if len(email or "") > 254 or not _EMAIL_RE.match(email or ""):
        return "邮箱格式不正确"
    return None


def validate_password(password: str) -> Optional[str]:
    p = password or ""
    if len(p) < 8:
        return "密码长度至少8位"
    if len(p) > 128:
        return "密码长度不能超过128位"
    if not (re.search(r"[A-Za-z]", p) and re.search(r"\d", p)):
        return "密码需同时包含字母和数字"
    return None


def require(body: dict, *keys: str) -> None:
    """Raise BAD_REQUEST if any key is missing/None in ``body``."""
    missing = [k for k in keys if body.get(k) is None]
    if missing:
        raise AppError(
            ErrorCode.BAD_REQUEST,
            f"Missing required field(s): {', '.join(missing)}",
            {"missing": missing},
        )


def as_number(value, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(
            ErrorCode.BAD_REQUEST, f"Field '{name}' must be a number", {"value": value}
        ) from exc


def map_display_to_device(
    display_x: float,
    display_y: float,
    display_width: float,
    display_height: float,
    device_width: float,
    device_height: float,
    _orientation: str = "PORTRAIT",
) -> Tuple[int, int]:
    """Map a point on the rendered image to device-pixel coordinates.

    Assumes the frame is shown with ``object-fit: contain`` (preserves aspect,
    centers, letterboxes). Steps:
      1. Compute the contain-fit scale + the centered content rect inside the
         display box (the area actually covered by the frame, sans black bars).
      2. Reject clicks that land on the letterbox bars.
      3. Convert to a 0..1 ratio within the content, then to device pixels.
    Orientation only affects which device dimension is "wide"; the frame the
    provider returns already matches the current orientation, so device_width/
    height should be passed as the *current* frame dimensions.
    """
    if min(display_width, display_height, device_width, device_height) <= 0:
        raise AppError(
            ErrorCode.COORDINATE_MAPPING_FAILED,
            "Non-positive dimension in coordinate mapping",
            {
                "display": [display_width, display_height],
                "device": [device_width, device_height],
            },
        )

    scale = min(display_width / device_width, display_height / device_height)
    content_w = device_width * scale
    content_h = device_height * scale
    offset_x = (display_width - content_w) / 2.0
    offset_y = (display_height - content_h) / 2.0

    # Clamp into the content rect (tolerate a 1px edge slop instead of erroring).
    rel_x = display_x - offset_x
    rel_y = display_y - offset_y
    rel_x = _clamp(rel_x, 0.0, content_w)
    rel_y = _clamp(rel_y, 0.0, content_h)

    ratio_x = rel_x / content_w
    ratio_y = rel_y / content_h
    dev_x = int(round(ratio_x * device_width))
    dev_y = int(round(ratio_y * device_height))

    dev_x = int(_clamp(dev_x, 0, device_width - 1))
    dev_y = int(_clamp(dev_y, 0, device_height - 1))
    return dev_x, dev_y


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
