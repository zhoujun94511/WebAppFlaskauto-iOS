"""SQLite layer for multi-user auth + device reservations.

Mirrors the Android sibling's schema so the two can later share a DB/service.
Three tables: users, user_sessions, device_reservations. ``init_db()`` is
idempotent (seeds accounts on first run) and wipes sessions + reservations on
every boot (no ghost logins/holds across restarts).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

from utils.logging_setup import get_logger

_log = get_logger(__name__)

# Python 3.12 deprecated sqlite3's built-in datetime adapter. Register an
# explicit one (matching the old default's space-separated ISO output, e.g.
# "2026-06-01 17:30:00.123456") so every datetime we pass to execute() keeps
# serializing identically — string comparisons against CURRENT_TIMESTAMP and
# datetime.fromisoformat() reads both stay valid. Registered once at import.
sqlite3.register_adapter(datetime, lambda val: val.isoformat(sep=" "))

DB_PATH = Path(os.environ.get("WEBAPP_DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "app.db")))

_initialised = False
_init_lock = threading.Lock()

# (username, email, plaintext password, role)
SEED_ACCOUNTS = [
    ("superadmin", "superadmin@local", "superadmin123", "super_admin"),
    ("admin", "admin@local", "admin123", "admin"),
    # A baseline normal-user account so the reservation/occupation flow can be
    # exercised out of the box (admins bypass reservation; this one must claim).
    ("user", "user@local", "user123", "user"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    session_token TEXT UNIQUE NOT NULL,
    expires_at    TIMESTAMP NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions (session_token);
CREATE TABLE IF NOT EXISTS device_reservations (
    device_id   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_password(password: str, salt: str) -> str:
    return generate_password_hash(password + salt)


def init_db() -> None:
    """Create tables + seed accounts (idempotent). Wipes sessions + reservations."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = get_conn()
        try:
            conn.executescript(_SCHEMA)
            # Fresh boot: drop stale sessions + reservations.
            conn.execute("DELETE FROM user_sessions")
            conn.execute("DELETE FROM device_reservations")
            for username, email, password, role in SEED_ACCOUNTS:
                exists = conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (username,)
                ).fetchone()
                if not exists:
                    import secrets

                    salt = secrets.token_hex(32)
                    conn.execute(
                        "INSERT INTO users (username, email, password_hash, salt, role) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (username, email, _hash_password(password, salt), salt, role),
                    )
                    _log.info("seeded account: %s (%s)", username, role)
            conn.commit()
            _initialised = True
            _log.info("database ready at %s", DB_PATH)
        finally:
            conn.close()


def now() -> datetime:
    return datetime.now()
