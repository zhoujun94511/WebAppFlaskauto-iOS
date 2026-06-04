"""Device reservations (occupy / release / ownership) for the shared pool.

Atomicity comes from the ``device_reservations`` PRIMARY KEY on device_id — a
racing second claim hits an IntegrityError. Expired holds are reaped lazily on
read and by a background sweeper. Releasing a device tears down its WDA/stream
so the next user starts clean. Mirrors the Android sibling's semantics.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Optional

from services import event_bus as events
from services.auth_service import is_admin
from services.app_db import get_conn
from services.runtime_state import state
from utils.logging_setup import get_logger

_log = get_logger(__name__)

MAX_RESERVATION_MINUTES = 240
DEFAULT_RESERVATION_MINUTES = 60
GRACE_SECONDS = 30
_SWEEP_INTERVAL_SECONDS = 10


class ReservationError(Exception):
    pass


def _parse(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts))


def _row(row) -> dict:
    return {
        "device_id": row["device_id"], "user_id": row["user_id"],
        "username": row["username"], "created_at": str(row["created_at"]),
        "expires_at": str(row["expires_at"]),
    }


def get(device_id: str) -> Optional[dict]:
    """Active reservation or None. Reaps it if expired past the grace buffer."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM device_reservations WHERE device_id = ?",
                           (device_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if _parse(row["expires_at"]) + timedelta(seconds=GRACE_SECONDS) < datetime.now():
        release(device_id, reason="expired")
        return None
    return _row(row)


def list_all() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM device_reservations").fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def _holds_another(user_id: int, device_id: str) -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT device_id FROM device_reservations WHERE user_id = ? AND device_id != ?",
            (user_id, device_id),
        ).fetchone()
    finally:
        conn.close()
    return row["device_id"] if row else None


def claim(device_id: str, user: dict, minutes: int = DEFAULT_RESERVATION_MINUTES) -> dict:
    if not state.get_device(device_id):
        raise ReservationError("设备不存在或已断开")
    minutes = max(1, min(int(minutes or DEFAULT_RESERVATION_MINUTES), MAX_RESERVATION_MINUTES))
    expires = datetime.now() + timedelta(minutes=minutes)

    existing = get(device_id)
    if existing and existing["user_id"] != user["id"]:
        raise ReservationError(f"设备已被 {existing['username']} 占用")
    if existing and existing["user_id"] == user["id"]:
        conn = get_conn()
        try:
            conn.execute("UPDATE device_reservations SET expires_at = ? WHERE device_id = ?",
                         (expires, device_id))
            conn.commit()
        finally:
            conn.close()
        events.emit("reservation_changed", {"device_id": device_id, "state": "extended"})
        return get(device_id)

    if not is_admin(user):
        other = _holds_another(user["id"], device_id)
        if other:
            raise ReservationError(f"你已占用另一台设备 ({other[:12]})，请先释放")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO device_reservations (device_id, user_id, username, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (device_id, user["id"], user["username"], expires),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        cur = get(device_id)
        raise ReservationError(f"设备已被 {cur['username'] if cur else '其他用户'} 占用")
    finally:
        with suppress(Exception):
            conn.close()
    events.emit("reservation_changed", {"device_id": device_id, "state": "claimed"})
    return get(device_id)


def release(device_id: str, *, reason: str = "released") -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM device_reservations WHERE device_id = ?", (device_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    _teardown(device_id)
    if deleted:
        events.emit("device_released", {"device_id": device_id, "reason": reason})
        _log.info("reservation released: %s (%s)", device_id[:12], reason)
    return deleted


def _teardown(device_id: str) -> None:
    """Stop streams + WebRTC + WDA for a released device so the next holder starts clean."""
    from services import get_adapter

    with suppress(Exception):
        from services import device_log
        device_log.stop_for_device(device_id)
    with suppress(Exception):
        adapter = get_adapter()
        with suppress(Exception):
            adapter.webrtc.stop_device(device_id)
        sess = state.streams.pop(device_id, None)
        if sess:
            with suppress(Exception):
                sess.stop()
        with suppress(Exception):
            adapter.disconnect(device_id)


def assert_owner(device_id: str, user: Optional[dict]) -> None:
    """Raise ReservationError unless ``user`` may control ``device_id``."""
    if not user:
        raise ReservationError("未登录")
    res = get(device_id)
    if res is None:
        if is_admin(user):
            return
        raise ReservationError("请先占用该设备再操作")
    if res["user_id"] == user["id"] or is_admin(user):
        return
    raise ReservationError(f"设备已被 {res['username']} 占用")


# ── background sweeper ────────────────────────────────────────────────
_sweeper_started = False


def sweep() -> int:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT device_id, expires_at FROM device_reservations").fetchall()
    finally:
        conn.close()
    n = 0
    cutoff = datetime.now() - timedelta(seconds=GRACE_SECONDS)
    for r in rows:
        if _parse(r["expires_at"]) < cutoff:
            release(r["device_id"], reason="expired")
            n += 1
    return n


def start_sweeper() -> None:
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True

    def _loop():
        while True:
            time.sleep(_SWEEP_INTERVAL_SECONDS)
            with suppress(Exception):
                sweep()

    threading.Thread(target=_loop, name="reservation-sweeper", daemon=True).start()
    _log.info("reservation sweeper started (every %ds)", _SWEEP_INTERVAL_SECONDS)
