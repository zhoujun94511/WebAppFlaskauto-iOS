"""White-box: user-field validators + body/number guards.

Mirrors the Android sibling's rules so a merged backend validates identically.
"""

from __future__ import annotations

import pytest

from services.request_validators import (
    as_number,
    normalize_email,
    normalize_username,
    require,
    validate_email,
    validate_password,
    validate_username,
)
from utils.app_errors import AppError, ErrorCode


# ── username ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["abc", "User_1", "a.b-c", "Z00", "x" * 32])
def test_valid_usernames(name):
    assert validate_username(name) is None


@pytest.mark.parametrize("name", ["ab", "", "_leading", ".dot", "x" * 33, "has space", "bad!char"])
def test_invalid_usernames(name):
    assert validate_username(name) is not None


def test_normalize_username_strips():
    assert normalize_username("  bob  ") == "bob"


# ── email ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("email", ["a@b.co", "user.name@sub.example.com"])
def test_valid_emails(email):
    assert validate_email(email) is None


@pytest.mark.parametrize("email", ["", "no-at", "a@b", "a b@c.com", "@c.com", "x@y." ])
def test_invalid_emails(email):
    assert validate_email(email) is not None


def test_email_too_long_rejected():
    assert validate_email("a" * 250 + "@x.com") is not None


def test_normalize_email_lowercases_and_strips():
    assert normalize_email("  USER@Example.COM ") == "user@example.com"


# ── password ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("pw", ["abc12345", "Passw0rd", "a1" * 4])
def test_valid_passwords(pw):
    assert validate_password(pw) is None


def test_password_too_short():
    assert validate_password("a1b2c3") is not None  # 6 chars


def test_password_too_long():
    assert validate_password("a1" * 65) is not None  # 130 chars


def test_password_must_mix_letter_and_digit():
    assert validate_password("alphabetonly") is not None
    assert validate_password("12345678") is not None


# ── require / as_number ──────────────────────────────────────────────
def test_require_passes_when_present():
    require({"a": 1, "b": "x"}, "a", "b")  # no raise


def test_require_raises_bad_request_with_missing_list():
    with pytest.raises(AppError) as ei:
        require({"a": 1, "b": None}, "a", "b", "c")
    assert ei.value.code == ErrorCode.BAD_REQUEST
    assert set(ei.value.detail["missing"]) == {"b", "c"}


def test_as_number_parses():
    assert as_number("3.5", "x") == 3.5
    assert as_number(7, "x") == 7.0


def test_as_number_rejects_non_number():
    with pytest.raises(AppError) as ei:
        as_number("not-a-number", "x1")
    assert ei.value.code == ErrorCode.BAD_REQUEST
    assert ei.value.detail["value"] == "not-a-number"
