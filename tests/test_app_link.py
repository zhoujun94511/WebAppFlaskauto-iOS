"""Full-link white-box: the live HTTP stack via Flask's test client.

Boots a real ``create_app()`` against an isolated temp DB (no hardware: the
device control layer is never reached because the gate rejects first, and the
device list reads in-memory state). Exercises the central before_request gate:

  * public endpoints open without a session,
  * protected endpoints → 401 + unified failure envelope,
  * login issues a cookie; check-auth confirms it,
  * reservation ownership gate → 403 for a non-owner, allowed after claim,
  * admin bypasses the reservation gate.
"""

from __future__ import annotations

import os
import tempfile

# Must be set before importing app/app_db so the module-level create_app()
# (run at import) also avoids touching the real data/app.db.
_TMP_DIR = tempfile.mkdtemp(prefix="ios-link-")
os.environ.setdefault("WEBAPP_DB_PATH", os.path.join(_TMP_DIR, "link.db"))
os.environ.setdefault("IOS_USE_GOIOS", "0")
os.environ.setdefault("OPEN_BROWSER", "0")

import pytest

from ios.device_models import IOSDevice
from services.runtime_state import state


@pytest.fixture
def client(tmp_path, monkeypatch):
    from services import app_db

    db_file = tmp_path / "link.db"
    monkeypatch.setattr(app_db, "DB_PATH", db_file)
    monkeypatch.setattr(app_db, "_initialised", False)

    import app as app_module

    flask_app, _socketio = app_module.create_app()
    flask_app.config.update(TESTING=True)
    state.devices.clear()
    yield flask_app.test_client()
    state.devices.clear()
    monkeypatch.setattr(app_db, "_initialised", False, raising=False)


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _uid(username):
    from services import app_db

    conn = app_db.get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


# ── public endpoints ─────────────────────────────────────────────────
def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["success"] is True


# ── auth gate ────────────────────────────────────────────────────────
def test_protected_endpoint_401_without_session(client):
    r = client.get("/api/devices")
    assert r.status_code == 401
    body = r.get_json()
    assert body["success"] is False and body["code"] == "UNAUTHORIZED"


def test_login_then_check_auth(client):
    r = _login(client, "admin", "admin123")
    assert r.status_code == 200 and r.get_json()["success"] is True
    ca = client.get("/api/auth/check-auth")
    assert ca.status_code == 200 and ca.get_json()["success"] is True


def test_login_wrong_password_does_not_authorize(client):
    assert _login(client, "admin", "WRONG").get_json()["success"] is False
    # still anonymous → protected endpoint blocked
    assert client.get("/api/devices").status_code == 401


def test_authed_devices_list_envelope(client):
    _login(client, "admin", "admin123")
    r = client.get("/api/devices")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert isinstance(body["data"]["devices"], list)


# ── reservation gate ─────────────────────────────────────────────────
def test_non_owner_control_denied(client):
    # Register + login a plain user.
    reg = client.post("/api/auth/register",
                      json={"username": "carol", "email": "carol@local.test", "password": "passw0rd"})
    assert reg.get_json()["success"] is True
    _login(client, "carol", "passw0rd")
    state.upsert_device(IOSDevice(udid="link-dev", name="iPhone", connected=True))

    r = client.post("/api/devices/link-dev/tap", json={"x": 1, "y": 1})
    assert r.status_code == 403
    assert r.get_json()["code"] == "RESERVATION_DENIED"


def test_owner_control_passes_gate_after_claim(client):
    client.post("/api/auth/register",
               json={"username": "dave", "email": "dave@local.test", "password": "passw0rd"})
    _login(client, "dave", "passw0rd")
    state.upsert_device(IOSDevice(udid="link-dev", name="iPhone", connected=True))

    from services import reservation_service as resv
    resv.claim("link-dev", {"id": _uid("dave"), "username": "dave", "role": "user"})

    r = client.post("/api/devices/link-dev/tap", json={"x": 1, "y": 1})
    # Gate passed → NOT a reservation rejection. (Handler may still error on the
    # absent real device, but it must not be 403/RESERVATION_DENIED.)
    assert r.status_code != 403
    assert (r.get_json() or {}).get("code") != "RESERVATION_DENIED"


def test_admin_bypasses_reservation_gate(client):
    _login(client, "admin", "admin123")
    state.upsert_device(IOSDevice(udid="link-dev", name="iPhone", connected=True))
    r = client.post("/api/devices/link-dev/tap", json={"x": 1, "y": 1})
    assert r.status_code != 403
    assert (r.get_json() or {}).get("code") != "RESERVATION_DENIED"
