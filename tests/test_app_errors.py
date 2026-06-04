"""White-box: unified response envelope + AppError → HTTP status mapping.

Every HTTP/Socket.IO payload uses exactly one of two shapes; these lock that
contract (and the code→status map the API layer relies on) in place.
"""

from __future__ import annotations

from utils.app_errors import AppError, ErrorCode, err, ok


# ── success envelope ─────────────────────────────────────────────────
def test_ok_default_shape():
    env = ok()
    assert env == {"success": True, "data": {}, "message": "ok"}


def test_ok_with_data_and_message():
    env = ok({"n": 1}, "done")
    assert env["success"] is True
    assert env["data"] == {"n": 1}
    assert env["message"] == "done"


def test_ok_none_data_becomes_empty_dict():
    # None data must not leak through as null — frontend expects an object.
    assert ok(None)["data"] == {}


def test_ok_preserves_falsey_non_none_data():
    # 0 / [] / "" are valid payloads and must NOT be coerced to {}.
    assert ok(0)["data"] == 0
    assert ok([])["data"] == []


# ── failure envelope ─────────────────────────────────────────────────
def test_err_shape():
    env = err(ErrorCode.WDA_NOT_RUNNING, "down", {"udid": "x"})
    assert env == {
        "success": False,
        "code": "WDA_NOT_RUNNING",
        "message": "down",
        "detail": {"udid": "x"},
    }


def test_err_defaults_message_to_code_and_detail_to_dict():
    env = err(ErrorCode.NO_IOS_DEVICE)
    assert env["message"] == "NO_IOS_DEVICE"
    assert env["detail"] == {}


# ── AppError ─────────────────────────────────────────────────────────
def test_apperror_to_dict_matches_err():
    e = AppError(ErrorCode.CONFLICT, "busy", {"a": 1})
    assert e.to_dict() == err(ErrorCode.CONFLICT, "busy", {"a": 1})


def test_apperror_message_falls_back_to_code():
    assert AppError(ErrorCode.FORBIDDEN).message == "FORBIDDEN"


def test_apperror_is_raisable_exception():
    try:
        raise AppError(ErrorCode.BAD_REQUEST, "nope")
    except AppError as exc:
        assert str(exc) == "nope"
        assert exc.code == "BAD_REQUEST"


# ── HTTP status mapping ──────────────────────────────────────────────
def test_status_map_for_known_codes():
    cases = {
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.CONFLICT: 409,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.RESERVATION_DENIED: 403,
        ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED: 503,
        ErrorCode.WDA_REQUEST_FAILED: 502,
        ErrorCode.INTERNAL_ERROR: 500,
        ErrorCode.WDA_NOT_RUNNING: 409,
    }
    for code, status in cases.items():
        assert AppError(code).http_status == status, code


def test_status_defaults_to_400_for_unmapped_code():
    assert AppError(ErrorCode.BAD_REQUEST).http_status == 400
    assert AppError("SOMETHING_NEW").http_status == 400


def test_explicit_http_status_overrides_map():
    assert AppError(ErrorCode.CONFLICT, http_status=418).http_status == 418
