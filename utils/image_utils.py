"""Image helpers: normalize WDA PNG screenshots to JPEG frames, base64, sizes."""

from __future__ import annotations

import base64
import io
from typing import Optional, Tuple

try:
    from PIL import Image, UnidentifiedImageError  # type: ignore

    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False
    UnidentifiedImageError = OSError


def to_jpeg(data: bytes, quality: int = 70) -> bytes:
    """Best-effort convert arbitrary image bytes (PNG from WDA) to JPEG.

    Falls back to returning the original bytes if Pillow is unavailable.
    The browser <img> can render PNG too, JPEG is just smaller on the wire.
    """
    if not _HAVE_PIL:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality)
        return out.getvalue()
    except (OSError, ValueError, AttributeError, UnidentifiedImageError):
        return data


def image_size(data: bytes) -> Optional[Tuple[int, int]]:
    if not _HAVE_PIL:
        return None
    try:
        return Image.open(io.BytesIO(data)).size  # (w, h)
    except (OSError, ValueError, AttributeError, UnidentifiedImageError):
        return None


def to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
