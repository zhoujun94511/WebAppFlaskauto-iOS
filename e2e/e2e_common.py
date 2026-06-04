"""Shared helper for the real-device e2e scripts now that auth is always on.

Logs in as the seeded admin (admin bypasses the reservation gate) and returns a
cookie-carrying requests.Session plus the Cookie header to hand to a Socket.IO
client (so user_from_socket() resolves on the websocket/polling handshake too).
"""

from __future__ import annotations

import requests


def admin_session(base: str) -> tuple[requests.Session, dict]:
    s = requests.Session()
    r = s.post(base + "/api/auth/login",
               json={"username": "admin", "password": "admin123"}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"[e2e] admin login failed: {r.status_code} {r.text[:120]}")
    cookie = s.cookies.get("session")
    return s, ({"Cookie": f"session={cookie}"} if cookie else {})
