"""Measure ACTUAL delivered MJPEG fps (counts stream:frame over the socket).

Reflects the WDA MJPEG source rate after the Tier-1 tuning (framerate/scaling/
quiescence). Compare against the old 15-cap config.

    .venv\\Scripts\\python.exe scripts\\spikes\\mjpeg_fps.py --udid <UDID> --seconds 6
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import requests
import socketio

# Set your device UDID via the --udid flag or the IOS_UDID env var
# (no real device identifier is hard-coded here).
DEFAULT_UDID = os.environ.get("IOS_UDID", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5099")
    ap.add_argument("--udid", default=DEFAULT_UDID)
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()

    s = requests.Session()
    s.post(a.base + "/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=15)
    s.delete(a.base + f"/api/reservations/{a.udid}", timeout=15)
    print(f"[connect] {a.udid[:16]} (cold ~15s)…", flush=True)
    cn = s.post(a.base + f"/api/devices/{a.udid}/connect", timeout=150)
    try:
        j = cn.json()
    except ValueError:
        j = {}
    if cn.status_code != 200:
        print(f"[abort] connect {cn.status_code} {j.get('code')} — {str(j.get('message'))[:80]}")
        return 1
    print("[connect] ok, wda_running=", (j.get("data") or {}).get("device", {}).get("wda_running"), flush=True)

    cookie = s.cookies.get("session")
    hdr = {"Cookie": f"session={cookie}"} if cookie else {}
    n = {"f": 0}
    sio = socketio.Client()

    @sio.event
    def connect():
        sio.emit("stream:start", {"udid": a.udid, "provider": "mjpeg", "fps": a.fps})

    @sio.on("stream:frame")
    def _f(d):
        if d and d.get("udid") == a.udid:
            n["f"] += 1

    @sio.on("stream:error")
    def _e(d):
        print("[stream:error]", d)

    sio.connect(a.base, transports=["polling"], headers=hdr)
    time.sleep(1.0)  # let the stream warm up
    n["f"] = 0       # count steady-state only
    t0 = time.time()
    time.sleep(a.seconds)
    elapsed = time.time() - t0
    frames = n["f"]
    with __import__("contextlib").suppress(Exception):
        sio.emit("stream:stop", {"udid": a.udid})
        time.sleep(0.3)
        sio.disconnect()
    print(f"\nMJPEG: {frames} frames in {elapsed:.1f}s = {frames/elapsed:.1f} fps  (requested {a.fps})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
