"""Unified error codes + API response helpers.

Every HTTP/Socket.IO payload uses one of two shapes:

    success: {"success": true,  "data": {...}, "message": "ok"}
    failure: {"success": false, "code": "...", "message": "...", "detail": {...}}

Business errors raise :class:`AppError` carrying a stable ``code`` from
:class:`ErrorCode`; the API layer turns it into the failure envelope.
"""

from __future__ import annotations

from typing import Any, Optional


class ErrorCode:
    """Stable machine-readable error codes (mirrored in the README + frontend)."""

    PYMOBILEDEVICE3_NOT_INSTALLED = "PYMOBILEDEVICE3_NOT_INSTALLED"
    NO_IOS_DEVICE = "NO_IOS_DEVICE"
    DEVICE_NOT_TRUSTED = "DEVICE_NOT_TRUSTED"
    DEVELOPER_MODE_DISABLED = "DEVELOPER_MODE_DISABLED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    PORT_FORWARD_FAILED = "PORT_FORWARD_FAILED"
    WDA_NOT_RUNNING = "WDA_NOT_RUNNING"
    WDA_REQUEST_FAILED = "WDA_REQUEST_FAILED"
    SCREEN_PROVIDER_FAILED = "SCREEN_PROVIDER_FAILED"
    MJPEG_STREAM_FAILED = "MJPEG_STREAM_FAILED"
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"
    COORDINATE_MAPPING_FAILED = "COORDINATE_MAPPING_FAILED"
    LOCAL_PORT_IN_USE = "LOCAL_PORT_IN_USE"
    IOS17_TUNNEL_FAILED = "IOS17_TUNNEL_FAILED"
    # Auth / multi-user / reservations
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    RESERVATION_DENIED = "RESERVATION_DENIED"
    # Generic fallbacks
    BAD_REQUEST = "BAD_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# HTTP status hints for common codes (the rest default to 400).
_HTTP_STATUS = {
    ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED: 503,
    ErrorCode.NO_IOS_DEVICE: 404,
    ErrorCode.DEVICE_DISCONNECTED: 409,
    ErrorCode.WDA_NOT_RUNNING: 409,
    ErrorCode.LOCAL_PORT_IN_USE: 409,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.WDA_REQUEST_FAILED: 502,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.RESERVATION_DENIED: 403,
}


class AppError(Exception):
    """A business error carrying a stable code + optional structured detail."""

    def __init__(
        self,
        code: str,
        message: str = "",
        detail: Optional[dict] = None,
        http_status: Optional[int] = None,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.detail = detail or {}
        self.http_status = http_status or _HTTP_STATUS.get(code, 400)

    def to_dict(self) -> dict:
        return {
            "success": False,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"success": True, "data": data if data is not None else {}, "message": message}


def err(code: str, message: str = "", detail: Optional[dict] = None) -> dict:
    return AppError(code, message, detail).to_dict()
