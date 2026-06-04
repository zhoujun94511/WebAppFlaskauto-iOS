"""Auth + user-management HTTP API (unified envelope via @api).

Login uses a httponly Flask session cookie carrying a server-side token.
Roles: super_admin > admin > user.
"""

from __future__ import annotations

from flask import Blueprint, request, session

from api import api
from services import auth_service as auth
from services.app_db import get_conn
from services.rate_limit import login_limiter
from services.request_validators import (
    normalize_email, normalize_username, require,
    validate_email, validate_password, validate_username,
)
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
_log = get_logger(__name__)


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _user_public(row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "email": row["email"],
        "role": row["role"], "is_active": bool(row["is_active"]),
        "created_at": row["created_at"], "last_login": row["last_login"],
    }


# ── auth ──────────────────────────────────────────────────────────────
@bp.post("/login")
@api
def login():
    body = _body()
    require(body, "username", "password")
    username = normalize_username(str(body["username"]))
    rl_key = f"login:{_client_ip()}:{username.lower()}"
    locked = login_limiter.retry_after(rl_key)
    if locked:
        # `detail.seconds` lets the frontend render a localized message with the
        # count (so the UI isn't pinned to this Chinese string).
        raise AppError(
            ErrorCode.RATE_LIMITED,
            f"登录尝试过于频繁，请约 {locked} 秒后再试",
            detail={"seconds": locked},
        )
    user = auth.authenticate(username, str(body["password"]))
    if user is None:
        login_limiter.record(rl_key)
        raise AppError(ErrorCode.UNAUTHORIZED, "用户名或密码错误")
    login_limiter.reset(rl_key)
    token = auth.create_session(user["id"])
    session["session_token"] = token
    session.permanent = True
    return {"user": user}


@bp.post("/register")
@api
def register():
    body = _body()
    require(body, "username", "email", "password")
    username, email, password = (
        normalize_username(str(body["username"])),
        normalize_email(str(body["email"])),
        str(body["password"]),
    )
    for err in (validate_username(username), validate_email(email), validate_password(password)):
        if err:
            raise AppError(ErrorCode.BAD_REQUEST, err)
    pwd_hash, salt = auth.new_password_fields(password)
    conn = get_conn()
    try:
        dup = conn.execute(
            "SELECT 1 FROM users WHERE username = ? OR email = ?", (username, email)
        ).fetchone()
        if dup:
            raise AppError(ErrorCode.CONFLICT, "用户名或邮箱已存在")
        conn.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) "
            "VALUES (?, ?, ?, ?, 'user')",
            (username, email, pwd_hash, salt),
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "注册成功，请登录"}


@bp.post("/logout")
@api
@auth.login_required
def logout():
    auth.destroy_session(session.get("session_token", ""))
    session.clear()
    return {"message": "ok"}


@bp.get("/check-auth")
@api
def check_auth():
    user = auth.current_user()
    return {"authenticated": user is not None, "user": user}


@bp.get("/profile")
@api
@auth.login_required
def profile():
    return {"user": auth.current_user()}


@bp.post("/change-password")
@api
@auth.login_required
def change_password():
    body = _body()
    require(body, "old_password", "new_password")
    user = auth.current_user()
    err = validate_password(str(body["new_password"]))
    if err:
        raise AppError(ErrorCode.BAD_REQUEST, err)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not auth.verify_password(row["password_hash"], row["salt"], str(body["old_password"])):
            raise AppError(ErrorCode.UNAUTHORIZED, "原密码错误")
        pwd_hash, salt = auth.new_password_fields(str(body["new_password"]))
        conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                     (pwd_hash, salt, user["id"]))
        conn.commit()
    finally:
        conn.close()
    auth.revoke_user_sessions(user["id"])
    session.clear()
    return {"message": "密码已修改，请重新登录"}


# ── user management (admin) ────────────────────────────────────────────
def _can_manage(actor: dict, target_role: str) -> bool:
    """super_admin manages anyone; admin manages only 'user'."""
    if actor["role"] == "super_admin":
        return True
    return actor["role"] == "admin" and target_role == "user"


@bp.get("/users")
@api
@auth.admin_required
def list_users():
    actor = auth.current_user()
    conn = get_conn()
    try:
        if actor["role"] == "super_admin":
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM users WHERE role = 'user' ORDER BY id").fetchall()
    finally:
        conn.close()
    return {"users": [_user_public(r) for r in rows]}


@bp.post("/users")
@api
@auth.admin_required
def create_user():
    actor = auth.current_user()
    body = _body()
    require(body, "username", "email", "password")
    role = str(body.get("role", "user"))
    if role not in ("user", "admin", "super_admin"):
        raise AppError(ErrorCode.BAD_REQUEST, "无效角色")
    if not _can_manage(actor, role):
        raise AppError(ErrorCode.FORBIDDEN, "无权创建该角色的账号")
    username, email, password = (
        normalize_username(str(body["username"])),
        normalize_email(str(body["email"])),
        str(body["password"]),
    )
    for err in (validate_username(username), validate_email(email), validate_password(password)):
        if err:
            raise AppError(ErrorCode.BAD_REQUEST, err)
    pwd_hash, salt = auth.new_password_fields(password)
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username = ? OR email = ?",
                        (username, email)).fetchone():
            raise AppError(ErrorCode.CONFLICT, "用户名或邮箱已存在")
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) VALUES (?, ?, ?, ?, ?)",
            (username, email, pwd_hash, salt, role),
        )
        conn.commit()
        uid = cur.lastrowid
    finally:
        conn.close()
    return {"user": {"id": uid, "username": username, "email": email, "role": role}}


@bp.put("/users/<int:user_id>")
@api
@auth.admin_required
def update_user(user_id: int):
    actor = auth.current_user()
    body = _body()
    conn = get_conn()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise AppError(ErrorCode.NOT_FOUND, "用户不存在")
        if not _can_manage(actor, target["role"]):
            raise AppError(ErrorCode.FORBIDDEN, "无权管理该账号")
        revoke = False
        if "email" in body:
            email = normalize_email(str(body["email"]))
            if validate_email(email):
                raise AppError(ErrorCode.BAD_REQUEST, "邮箱格式不正确")
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        if "role" in body:
            new_role = str(body["role"])
            if new_role not in ("user", "admin", "super_admin") or not _can_manage(actor, new_role):
                raise AppError(ErrorCode.FORBIDDEN, "无权设置该角色")
            if user_id == actor["id"]:
                raise AppError(ErrorCode.FORBIDDEN, "不能修改自己的角色")
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        if "password" in body:
            err = validate_password(str(body["password"]))
            if err:
                raise AppError(ErrorCode.BAD_REQUEST, err)
            pwd_hash, salt = auth.new_password_fields(str(body["password"]))
            conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                         (pwd_hash, salt, user_id))
            revoke = True
        if "is_active" in body:
            active = 1 if body["is_active"] else 0
            if active == 0 and target["role"] == "super_admin":
                raise AppError(ErrorCode.FORBIDDEN, "不能停用超级管理员")
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (active, user_id))
            if active == 0:
                revoke = True
        conn.commit()
    finally:
        conn.close()
    if revoke:
        auth.revoke_user_sessions(user_id)
    return {"message": "ok"}


@bp.delete("/users/<int:user_id>")
@api
@auth.admin_required
def delete_user(user_id: int):
    actor = auth.current_user()
    if user_id == actor["id"]:
        raise AppError(ErrorCode.FORBIDDEN, "不能删除自己")
    conn = get_conn()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise AppError(ErrorCode.NOT_FOUND, "用户不存在")
        if target["role"] == "super_admin":
            raise AppError(ErrorCode.FORBIDDEN, "不能删除超级管理员")
        if not _can_manage(actor, target["role"]):
            raise AppError(ErrorCode.FORBIDDEN, "无权删除该账号")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "ok"}
