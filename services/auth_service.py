"""Authentication: password hashing, server-side sessions, current-user
resolution, and role decorators. Session token lives in the Flask session
cookie (httponly) and is validated against the ``user_sessions`` table — so
sessions are revocable (logout, password change, deactivate)."""

from __future__ import annotations

import functools
import secrets
from datetime import datetime, timedelta
from typing import Optional

from flask import g, session
from werkzeug.security import check_password_hash

from services.app_db import _hash_password, get_conn
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)
SESSION_TTL_HOURS = 24


def _row_to_user(row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "email": row["email"],
        "role": row["role"], "is_active": bool(row["is_active"]),
    }


# ── password ─────────────────────────────────────────────────────────
def new_password_fields(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(32)
    return _hash_password(password, salt), salt


def verify_password(stored_hash: str, salt: str, password: str) -> bool:
    return check_password_hash(stored_hash, password + salt)


# ── authentication ───────────────────────────────────────────────────
def authenticate(username: str, password: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if not row or not verify_password(row["password_hash"], row["salt"], password):
            return None
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.now(), row["id"]))
        conn.commit()
        return _row_to_user(row)
    finally:
        conn.close()


# ── sessions ─────────────────────────────────────────────────────────
def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_sessions (user_id, session_token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, datetime.now() + timedelta(hours=SESSION_TTL_HOURS)),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def revoke_user_sessions(user_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def validate_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (datetime.now(),))
        conn.commit()
        row = conn.execute(
            "SELECT u.id, u.username, u.email, u.role, u.is_active "
            "FROM users u JOIN user_sessions s ON u.id = s.user_id "
            "WHERE s.session_token = ? AND u.is_active = 1 AND s.expires_at > ?",
            (token, datetime.now()),
        ).fetchone()
        return _row_to_user(row) if row else None
    finally:
        conn.close()


# ── current user ─────────────────────────────────────────────────────
def current_user() -> Optional[dict]:
    if "current_user" in g.__dict__:
        return g.current_user
    user = validate_token(session.get("session_token", ""))
    g.current_user = user
    return user


def user_from_socket() -> Optional[dict]:
    """Resolve the user for a Socket.IO handler (no g caching)."""
    return validate_token(session.get("session_token", ""))


def is_admin(user: Optional[dict]) -> bool:
    return bool(user) and user.get("role") in ("admin", "super_admin")


# ── decorators (raise AppError → mapped by @api) ─────────────────────
def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            raise AppError(ErrorCode.UNAUTHORIZED, "未登录或会话已过期")
        return fn(*args, **kwargs)
    return wrapper


def _role_required(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                raise AppError(ErrorCode.UNAUTHORIZED, "未登录或会话已过期")
            if user.get("role") not in roles:
                raise AppError(ErrorCode.FORBIDDEN, "权限不足")
            return fn(*args, **kwargs)
        return wrapper
    return deco


def admin_required(fn):
    return _role_required("admin", "super_admin")(fn)


def super_admin_required(fn):
    return _role_required("super_admin")(fn)
