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


def test_closed_werkzeug_websocket_does_not_return_an_empty_body():
    from app import _WerkzeugClosedWebsocket

    def bare(_environ, _start_response):
        return []

    wrapped = _WerkzeugClosedWebsocket(bare)
    with pytest.raises(ConnectionAbortedError):
        wrapped(
            {"HTTP_UPGRADE": "websocket", "werkzeug.socket": object()},
            lambda *_a, **_k: None,
        )


def test_http_and_started_websocket_pass_through():
    from app import _WerkzeugClosedWebsocket

    def started(environ, start_response):
        start_response("200 OK", [])
        return [b"ok"]

    def forgotten(_environ, _start_response):
        return []

    wrapped = _WerkzeugClosedWebsocket(started)
    seen = {}

    def start(status, headers, exc_info=None):
        seen["status"] = status

    assert wrapped({"HTTP_UPGRADE": "websocket", "werkzeug.socket": object()}, start) == [b"ok"]
    assert seen["status"] == "200 OK"
    assert _WerkzeugClosedWebsocket(forgotten)({}, lambda *_a, **_k: None) == []


def test_werkzeug_hides_socketio_polling():
    import logging
    import sys

    from utils.logging_setup import setup_logging

    setup_logging()
    log = logging.getLogger("werkzeug")
    polling = logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1,
        '127.0.0.1 - - "GET /socket.io/?EIO=4&transport=polling HTTP/1.1" 200 -',
        (), None,
    )
    api = logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1,
        '127.0.0.1 - - "POST /api/auth/login HTTP/1.1" 200 -',
        (), None,
    )
    assert not log.filter(polling)
    assert log.filter(api)
    root = logging.getLogger()
    info = logging.LogRecord("app", logging.INFO, __file__, 1, "hello", (), None)
    warning = logging.LogRecord("app", logging.WARNING, __file__, 1, "hello", (), None)
    info_streams = [
        handler.stream for handler in root.handlers
        if getattr(handler, "stream", None) in (sys.stdout, sys.stderr)
        and info.levelno >= handler.level and handler.filter(info)
    ]
    warning_streams = [
        handler.stream for handler in root.handlers
        if getattr(handler, "stream", None) in (sys.stdout, sys.stderr)
        and warning.levelno >= handler.level and handler.filter(warning)
    ]
    assert info_streams == [sys.stdout]
    assert warning_streams == [sys.stderr]
