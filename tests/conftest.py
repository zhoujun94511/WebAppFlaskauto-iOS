"""Shared pytest fixtures for the white-box service tests.

``app_db_temp`` redirects the SQLite layer at an isolated temp file (so tests
never touch data/app.db), re-seeds it, and tears the singleton state back down
afterward. ``make_user`` inserts an arbitrary-role account for tests that need
a non-seed user.
"""

from __future__ import annotations

import secrets

import pytest


@pytest.fixture
def app_db_temp(tmp_path, monkeypatch):
    """Point the DB layer at a fresh temp file and seed it."""
    from services import app_db

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app_db, "DB_PATH", db_file)
    monkeypatch.setattr(app_db, "_initialised", False)
    app_db.init_db()
    yield app_db
    # _initialised + DB_PATH are restored by monkeypatch; the temp file dies
    # with tmp_path. Reset the module flag so a later real boot re-inits.
    monkeypatch.setattr(app_db, "_initialised", False, raising=False)


def make_user(app_db, username: str, password: str, role: str = "user",
              email: str | None = None) -> dict:
    """Insert a user directly (bypassing the register endpoint) and return it."""
    from werkzeug.security import generate_password_hash

    salt = secrets.token_hex(32)
    conn = app_db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, email or f"{username}@local",
             generate_password_hash(password + salt), salt, role),
        )
        conn.commit()
        uid = cur.lastrowid
    finally:
        conn.close()
    return {"id": uid, "username": username, "email": email or f"{username}@local",
            "role": role, "is_active": True}
