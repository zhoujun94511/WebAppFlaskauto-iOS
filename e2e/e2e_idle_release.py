"""Idle auto-release real-device test (zero mock).

With a short IOS_DEVICE_IDLE_TIMEOUT, verifies:
  * a connected device with NO viewers is auto-released (Disconnect) once idle;
  * a device WITH a viewer (active stream) is NOT released while watched.
Skips if no device attached.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # project root for utils./app imports

import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import cast

import requests
import socketio

from e2e_common import admin_session

ROOT = Path(__file__).resolve().parent.parent
PORT, TUNNEL_PORT = 5097, 28297
IDLE = 8
BASE = f"http://127.0.0.1:{PORT}"
_fails: list[str] = []
S: requests.Session | None = None  # logged-in admin session (auth always on)
COOKIE: dict = {}


def _session() -> requests.Session:
    return cast(requests.Session, S)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""), flush=True)
    if not cond:
        _fails.append(name)


def start_backend() -> subprocess.Popen:
    env = os.environ.copy()
    env.update(PORT=str(PORT), HOST="127.0.0.1", OPEN_BROWSER="0",
               IOS_GOIOS_TUNNEL_INFO_PORT=str(TUNNEL_PORT), IOS_LOCAL_PORT_START="18300",
               IOS_DEVICE_IDLE_TIMEOUT=str(IDLE))
    print(f"[boot] backend on :{PORT} (idle timeout {IDLE}s)…", flush=True)
    p = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            if requests.get(BASE + "/api/health", timeout=2).status_code == 200:
                return p
        except requests.RequestException:
            time.sleep(1)
    p.kill(); raise SystemExit("[boot] backend not healthy")


def teardown(p: subprocess.Popen) -> None:
    try:
        p.terminate(); p.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        p.kill()
    with suppress(Exception):
        from utils.port_utils import kill_listeners
        kill_listeners(PORT); kill_listeners(TUNNEL_PORT)


def connected(udid: str) -> bool:
    ds = _session().get(BASE + "/api/devices?rescan=1", timeout=30).json()["data"]["devices"]
    return next((d["connected"] for d in ds if d["udid"] == udid), False)


def run() -> None:
    ds = _session().get(BASE + "/api/devices?rescan=1", timeout=30).json()["data"]["devices"]
    if not ds:
        print("  SKIP: no device attached", flush=True); return
    udid = ds[0]["udid"]

    print("\n=== idle WITH no viewer → must auto-release ===", flush=True)
    _session().post(f"{BASE}/api/devices/{udid}/connect", timeout=150)
    check("connected after connect", connected(udid))
    print(f"  idling {IDLE + 7}s with no viewers…", flush=True)
    time.sleep(IDLE + 7)
    check("auto-released after idle (connected=False)", connected(udid) is False)

    print("\n=== viewer present → must NOT be released ===", flush=True)
    _session().post(f"{BASE}/api/devices/{udid}/connect", timeout=150)
    sio = socketio.Client()

    @sio.event
    def connect():
        sio.emit("stream:start", {"udid": udid, "provider": "mjpeg", "fps": 10})

    sio.connect(BASE, transports=["polling"], headers=COOKIE)
    print(f"  watching (stream open) for {IDLE + 7}s…", flush=True)
    time.sleep(IDLE + 7)
    check("still connected while watched", connected(udid) is True)
    sio.emit("stream:stop", {"udid": udid})
    time.sleep(0.3)
    sio.disconnect()
    _session().post(f"{BASE}/api/devices/{udid}/disconnect", timeout=30)


def main() -> int:
    p = start_backend()
    global S, COOKIE
    S, COOKIE = admin_session(BASE)
    try:
        run()
    finally:
        teardown(p)
    print("\n" + "=" * 50, flush=True)
    print(f"RESULT: {'PASS' if not _fails else 'FAIL ' + str(_fails)}", flush=True)
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
