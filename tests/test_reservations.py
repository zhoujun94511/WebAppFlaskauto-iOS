"""White-box: device reservations (claim / extend / release / ownership / sweep).

Atomic claim is enforced by the device_reservations PRIMARY KEY; admins are
exempt from the one-device-per-user limit and the ownership gate. Uses the
isolated DB fixture + real users (the reservation FK references users.id).
``_teardown`` is a no-op here (get_adapter() is uninitialised → suppressed),
so no real hardware is touched.
"""

from __future__ import annotations

import pytest

from conftest import make_user
from ios.device_models import IOSDevice
from services import reservation_service as resv
from services.runtime_state import state


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    state.devices.clear()
    yield
    state.devices.clear()


def _device(udid: str) -> str:
    state.upsert_device(IOSDevice(udid=udid, name="iPhone", connected=True))
    return udid


# ── claim ────────────────────────────────────────────────────────────
def test_claim_unknown_device_raises(app_db_temp):
    user = make_user(app_db_temp, "alice", "passw0rd")
    with pytest.raises(resv.ReservationError):
        resv.claim("no-such-udid", user)


def test_claim_success(app_db_temp):
    user = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    r = resv.claim(udid, user)
    assert r["device_id"] == udid and r["user_id"] == user["id"]
    assert resv.get(udid)["username"] == "alice"


def test_same_user_reclaim_extends(app_db_temp):
    user = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    first = resv.claim(udid, user, minutes=10)
    second = resv.claim(udid, user, minutes=60)
    assert second["user_id"] == user["id"]
    assert second["expires_at"] >= first["expires_at"]  # extended, not duplicated
    assert len(resv.list_all()) == 1


def test_other_user_claim_denied(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    bob = make_user(app_db_temp, "bob", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    with pytest.raises(resv.ReservationError) as ei:
        resv.claim(udid, bob)
    assert "alice" in str(ei.value)


def test_non_admin_limited_to_one_device(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    _device("dev-A")
    _device("dev-B")
    resv.claim("dev-A", alice)
    with pytest.raises(resv.ReservationError):
        resv.claim("dev-B", alice)  # already holds dev-A


def test_admin_may_hold_multiple(app_db_temp):
    admin = make_user(app_db_temp, "boss", "passw0rd", role="admin")
    _device("dev-A")
    _device("dev-B")
    resv.claim("dev-A", admin)
    resv.claim("dev-B", admin)  # no raise — admin exempt
    assert {r["device_id"] for r in resv.list_all()} == {"dev-A", "dev-B"}


# ── release ──────────────────────────────────────────────────────────
def test_release_removes_and_is_idempotent(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    assert resv.release(udid) is True
    assert resv.get(udid) is None
    assert resv.release(udid) is False  # nothing left to release


# ── assert_owner ─────────────────────────────────────────────────────
def test_assert_owner_no_user_raises(app_db_temp):
    with pytest.raises(resv.ReservationError):
        resv.assert_owner("dev-A", None)


def test_assert_owner_owner_ok(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    resv.assert_owner(udid, alice)  # no raise


def test_assert_owner_other_user_denied(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    bob = make_user(app_db_temp, "bob", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    with pytest.raises(resv.ReservationError):
        resv.assert_owner(udid, bob)


def test_assert_owner_unreserved_requires_claim_for_user(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    _device("dev-A")
    with pytest.raises(resv.ReservationError):
        resv.assert_owner("dev-A", alice)  # must claim first


def test_assert_owner_admin_exempt_even_unreserved(app_db_temp):
    admin = make_user(app_db_temp, "boss", "passw0rd", role="admin")
    _device("dev-A")
    resv.assert_owner("dev-A", admin)  # no raise — admin bypasses the gate


def test_assert_owner_admin_can_take_others_device(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    admin = make_user(app_db_temp, "boss", "passw0rd", role="admin")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    resv.assert_owner(udid, admin)  # admin overrides alice's hold


# ── sweep / expiry ───────────────────────────────────────────────────
def test_sweep_reaps_expired(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    conn = app_db_temp.get_conn()
    conn.execute("UPDATE device_reservations SET expires_at = '2000-01-01 00:00:00' "
                 "WHERE device_id = ?", (udid,))
    conn.commit()
    conn.close()
    assert resv.sweep() == 1
    assert resv.list_all() == []


def test_get_reaps_expired_lazily(app_db_temp):
    alice = make_user(app_db_temp, "alice", "passw0rd")
    udid = _device("dev-A")
    resv.claim(udid, alice)
    conn = app_db_temp.get_conn()
    conn.execute("UPDATE device_reservations SET expires_at = '2000-01-01 00:00:00' "
                 "WHERE device_id = ?", (udid,))
    conn.commit()
    conn.close()
    assert resv.get(udid) is None  # expired past grace → reaped on read
