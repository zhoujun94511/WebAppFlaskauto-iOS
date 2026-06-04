"""White-box: authentication — password hashing, server-side revocable
sessions, current-user resolution, and the role decorators.

Uses the ``app_db_temp`` fixture (isolated SQLite, seeded admin/superadmin).
Decorator tests run inside a Flask request context so g + session work.
"""

from __future__ import annotations

import pytest
from flask import Flask, session

from conftest import make_user  # tests/ is on sys.path via root conftest
from services import auth_service as auth
from utils.app_errors import AppError, ErrorCode


# ── password hashing ─────────────────────────────────────────────────
def test_hash_then_verify_roundtrip():
    h, salt = auth.new_password_fields("s3cret-pw")
    assert auth.verify_password(h, salt, "s3cret-pw") is True
    assert auth.verify_password(h, salt, "wrong") is False


def test_salt_makes_hashes_unique():
    h1, s1 = auth.new_password_fields("same")
    h2, s2 = auth.new_password_fields("same")
    assert s1 != s2 and h1 != h2  # 32-byte random salt per call


# ── authenticate ─────────────────────────────────────────────────────
def test_authenticate_seed_admin(app_db_temp):
    user = auth.authenticate("admin", "admin123")
    assert user and user["username"] == "admin" and user["role"] == "admin"


def test_authenticate_wrong_password(app_db_temp):
    assert auth.authenticate("admin", "nope") is None


def test_authenticate_unknown_user(app_db_temp):
    assert auth.authenticate("ghost", "whatever") is None


def test_authenticate_inactive_user_denied(app_db_temp):
    u = make_user(app_db_temp, "dormant", "passw0rd")
    conn = app_db_temp.get_conn()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (u["id"],))
    conn.commit()
    conn.close()
    assert auth.authenticate("dormant", "passw0rd") is None


# ── sessions ─────────────────────────────────────────────────────────
def test_session_create_validate_destroy(app_db_temp):
    u = auth.authenticate("admin", "admin123")
    token = auth.create_session(u["id"])
    assert auth.validate_token(token)["username"] == "admin"
    auth.destroy_session(token)
    assert auth.validate_token(token) is None


def test_validate_empty_token_is_none(app_db_temp):
    assert auth.validate_token("") is None
    assert auth.validate_token("garbage-token") is None


def test_revoke_user_sessions_kills_all(app_db_temp):
    u = auth.authenticate("admin", "admin123")
    t1, t2 = auth.create_session(u["id"]), auth.create_session(u["id"])
    auth.revoke_user_sessions(u["id"])
    assert auth.validate_token(t1) is None
    assert auth.validate_token(t2) is None


def test_expired_session_rejected(app_db_temp):
    u = auth.authenticate("admin", "admin123")
    token = auth.create_session(u["id"])
    # Force expiry into the past.
    conn = app_db_temp.get_conn()
    conn.execute("UPDATE user_sessions SET expires_at = '2000-01-01 00:00:00' "
                 "WHERE session_token = ?", (token,))
    conn.commit()
    conn.close()
    assert auth.validate_token(token) is None


def test_session_invalid_after_user_deactivated(app_db_temp):
    u = make_user(app_db_temp, "tempuser", "passw0rd")
    token = auth.create_session(u["id"])
    assert auth.validate_token(token) is not None
    conn = app_db_temp.get_conn()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (u["id"],))
    conn.commit()
    conn.close()
    assert auth.validate_token(token) is None


# ── is_admin ─────────────────────────────────────────────────────────
def test_is_admin_roles():
    assert auth.is_admin({"role": "admin"}) is True
    assert auth.is_admin({"role": "super_admin"}) is True
    assert auth.is_admin({"role": "user"}) is False
    assert auth.is_admin(None) is False


# ── decorators (need Flask request context) ──────────────────────────
@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def _logged_in_ctx(flask_app, _app_db, username, password):
    user = auth.authenticate(username, password)
    token = auth.create_session(user["id"])
    ctx = flask_app.test_request_context()
    ctx.push()
    session["session_token"] = token
    return ctx


def test_login_required_passes_when_authed(app_db_temp, flask_app):
    ctx = _logged_in_ctx(flask_app, app_db_temp, "admin", "admin123")
    try:
        called = auth.login_required(lambda: "ok")()
        assert called == "ok"
    finally:
        ctx.pop()


def test_login_required_raises_when_anonymous(app_db_temp, flask_app):
    ctx = flask_app.test_request_context()
    ctx.push()
    try:
        with pytest.raises(AppError) as ei:
            auth.login_required(lambda: "ok")()
        assert ei.value.code == ErrorCode.UNAUTHORIZED
    finally:
        ctx.pop()


def test_admin_required_allows_admin(app_db_temp, flask_app):
    ctx = _logged_in_ctx(flask_app, app_db_temp, "admin", "admin123")
    try:
        assert auth.admin_required(lambda: "ok")() == "ok"
    finally:
        ctx.pop()


def test_admin_required_forbids_plain_user(app_db_temp, flask_app):
    make_user(app_db_temp, "joe", "passw0rd", role="user")
    ctx = _logged_in_ctx(flask_app, app_db_temp, "joe", "passw0rd")
    try:
        with pytest.raises(AppError) as ei:
            auth.admin_required(lambda: "ok")()
        assert ei.value.code == ErrorCode.FORBIDDEN
    finally:
        ctx.pop()


def test_super_admin_required_forbids_admin(app_db_temp, flask_app):
    ctx = _logged_in_ctx(flask_app, app_db_temp, "admin", "admin123")
    try:
        with pytest.raises(AppError) as ei:
            auth.super_admin_required(lambda: "ok")()
        assert ei.value.code == ErrorCode.FORBIDDEN
    finally:
        ctx.pop()
