"""Multi-device real-device test: switching phones + start/stop within one
backend process lifetime (zero mock). Skips if fewer than 2 devices attached.

Guards the regressions found while debugging WSAENOTSOCK:
  * STOP must be clean — a stopped stream must NOT auto-resurrect (the close
    race was misclassified as "unhealthy" → self-heal restarted it).
  * Switching back to a device must re-stream even if its WDA died meanwhile
    (start_stream re-establishes WDA before streaming).
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # project root for utils./app imports

import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import cast

import requests
import socketio

from e2e_common import admin_session

ROOT = Path(__file__).resolve().parent.parent
PORT, TUNNEL_PORT = 5098, 28298
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
               IOS_GOIOS_TUNNEL_INFO_PORT=str(TUNNEL_PORT), IOS_LOCAL_PORT_START="18400")
    print(f"[boot] backend on :{PORT}…", flush=True)
    p = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            if requests.get(BASE + "/api/health", timeout=2).status_code == 200:
                return p
        except requests.RequestException:
            time.sleep(1)
    p.kill()
    raise SystemExit("[boot] backend did not become healthy")


def teardown(p: subprocess.Popen) -> None:
    try:
        p.terminate(); p.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        p.kill()
    with suppress(Exception):
        from utils.port_utils import kill_listeners
        kill_listeners(PORT); kill_listeners(TUNNEL_PORT)


def run() -> None:
    devs = _session().get(BASE + "/api/devices?rescan=1", timeout=30).json()["data"]["devices"]
    print(f"  devices: {[d['udid'][:12] for d in devs]}", flush=True)
    if len(devs) < 2:
        print("  SKIP: needs >=2 attached devices", flush=True)
        return
    a, b = devs[0]["udid"], devs[1]["udid"]

    counts: dict[str, int] = {}
    lock = threading.Lock()
    sio = socketio.Client()

    @sio.on("stream:frame")
    def _f(d):
        u = d.get("udid")
        if u:
            with lock:
                counts[u] = counts.get(u, 0) + 1

    sio.connect(BASE, transports=["polling"], headers=COOKIE)

    def n(u):
        with lock:
            return counts.get(u, 0)

    def connect(u):
        _session().post(f"{BASE}/api/devices/{u}/connect", timeout=150)

    print("\n=== device A: connect + stream ===", flush=True)
    connect(a)
    sio.emit("stream:start", {"udid": a, "provider": "mjpeg", "fps": 12})
    time.sleep(4)
    check("A streams", n(a) > 0, f"{n(a)} frames")

    print("\n=== STOP A must be clean (no auto-resurrect) ===", flush=True)
    sio.emit("stream:stop", {"udid": a})
    time.sleep(1)
    at = n(a)
    time.sleep(5)
    check("A stays stopped", n(a) == at, f"{at} -> {n(a)}")

    print("\n=== switch to B ===", flush=True)
    connect(b)
    sio.emit("stream:start", {"udid": b, "provider": "mjpeg", "fps": 12})
    time.sleep(4)
    check("B streams after switch", n(b) > 0, f"{n(b)} frames")

    print("\n=== switch back to A (re-stream in same process) ===", flush=True)
    sio.emit("stream:stop", {"udid": b})
    time.sleep(1)
    a0 = n(a)
    sio.emit("stream:start", {"udid": a, "provider": "mjpeg", "fps": 12})
    time.sleep(6)
    check("A re-streams after switching back", n(a) > a0, f"{a0} -> {n(a)}")

    sio.emit("stream:stop", {"udid": a})
    time.sleep(1)
    sio.disconnect()


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
