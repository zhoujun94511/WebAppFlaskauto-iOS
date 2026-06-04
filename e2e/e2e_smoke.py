"""真机端到端冒烟测试 —— 零 mock，走完整真实链路。

启一套真实后端（隔离端口，不碰你正在跑的 5001/28100），对**真机**依次验证：
    1. 扫描     -> 设备 connected=False（不再谎报已连接）
    2. connect  -> 自动起 go-ios 隧道 + runwda，WDA 真正 answer /status
    3. rescan   -> 已连接状态被保留（不被周期扫描打回 False）
    4. 控制     -> tap / 截图 走真 WDA，HTTP 200
    5. 开流     -> 真 MJPEG 帧通过 socket 推回，帧数 > 0
    6. 断开     -> 状态清干净

没插真机就直接判定 FAIL（这是真机测试，不存在“理想情况”）。

用法：
    .venv\\Scripts\\python.exe e2e_smoke.py
    .venv\\Scripts\\python.exe e2e_smoke.py --udid <UDID>   # 指定设备
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # project root for utils./app imports

import argparse
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
PORT = 5099                     # 隔离的后端端口
TUNNEL_PORT = 28299             # 隔离的 go-ios 隧道端口（不碰默认 28100）
BASE = f"http://127.0.0.1:{PORT}"

S: requests.Session | None = None  # logged-in admin session (auth is always on)
COOKIE: dict = {}           # Cookie header for the Socket.IO client

_PASS, _FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{_PASS if cond else _FAIL}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        _failures.append(name)


def _session() -> requests.Session:
    return cast(requests.Session, S)


def start_backend() -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        PORT=str(PORT), HOST="127.0.0.1", OPEN_BROWSER="0",
        IOS_GOIOS_TUNNEL_INFO_PORT=str(TUNNEL_PORT),
        # Isolated local-forward range (mjpeg base = +1000 = 19500) so the
        # self-heal step's port-kill can't disturb a real dev backend on 5001.
        IOS_LOCAL_PORT_START="18500",
    )
    print(f"[boot] starting REAL backend on :{PORT} (tunnel :{TUNNEL_PORT})…", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "app.py"], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            if requests.get(BASE + "/api/health", timeout=2).status_code == 200:
                print("[boot] backend healthy", flush=True)
                return proc
        except requests.RequestException:
            pass
        time.sleep(1)
    proc.kill()
    raise SystemExit("[boot] backend did not become healthy")


def teardown(proc: subprocess.Popen) -> None:
    print("[teardown] stopping backend + reclaiming go-ios…", flush=True)
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()
    # backend's SIGTERM hook stops its own tunnel; belt-and-suspenders:
    with suppress(Exception):
        from utils.port_utils import kill_listeners
        kill_listeners(PORT)
        kill_listeners(TUNNEL_PORT)


def devices() -> list[dict]:
    return _session().get(BASE + "/api/devices?rescan=1", timeout=30).json()["data"]["devices"]


def run(udid_arg: str | None) -> None:
    print("\n=== 1) scan (real usbmux) ===", flush=True)
    devs = devices()
    print(f"  devices: {[(d['udid'][:12], 'connected=' + str(d['connected'])) for d in devs]}", flush=True)
    check("a device is attached", bool(devs), "plug in a real iPhone over USB")
    if not devs:
        return
    udid = udid_arg or devs[0]["udid"]
    target = next((d for d in devs if d["udid"] == udid), None)
    check("target device present", target is not None, udid[:12])
    if not target:
        return
    check("freshly scanned device is connected=False", target["connected"] is False,
          "scan must NOT report app-level connected")

    print("\n=== 2) connect (auto-launch WDA via go-ios, no admin) ===", flush=True)
    t0 = time.time()
    body = _session().post(f"{BASE}/api/devices/{udid}/connect", timeout=150).json()
    dev = (body.get("data") or {}).get("device") or {}
    print(f"  connect in {time.time() - t0:.1f}s -> connected={dev.get('connected')} "
          f"wda_running={dev.get('wda_running')} port={dev.get('local_wda_port')}", flush=True)
    check("connect succeeded", bool(body.get("success")))
    check("device now connected", dev.get("connected") is True)
    check("WDA is running", dev.get("wda_running") is True)
    check("WDA local port assigned", bool(dev.get("local_wda_port")))

    print("\n=== 3) rescan must preserve app-level connected ===", flush=True)
    after = next((d for d in devices() if d["udid"] == udid), {})
    check("rescan kept connected=True", after.get("connected") is True,
          "periodic rescan must not clobber runtime state")

    print("\n=== 4) real WDA control ===", flush=True)
    sc = _session().post(f"{BASE}/api/devices/{udid}/screenshot", timeout=30)
    check("screenshot via real WDA -> 200", sc.status_code == 200, f"HTTP {sc.status_code}")
    t = time.time()
    sw = _session().post(
        f"{BASE}/api/devices/{udid}/swipe",
        json={"x1": 215, "y1": 650, "x2": 215, "y2": 300,
              "display_width": 430, "display_height": 932, "duration": 0.3},
        timeout=30,
    )
    dt = time.time() - t
    check("swipe via real WDA -> 200", sw.status_code == 200, f"HTTP {sw.status_code}")
    check("single swipe completes promptly (<3s)", dt < 3.0, f"{dt:.2f}s")

    print("\n=== 5) live stream (real MJPEG frames over socket) ===", flush=True)
    import threading as _th
    st = {"n": 0, "provider": None}
    _lk = _th.Lock()
    sio = socketio.Client()

    @sio.event
    def connect():
        sio.emit("stream:start", {"udid": udid, "provider": "mjpeg", "fps": 12})

    @sio.on("stream:frame")
    def _f(d):
        if d and d.get("udid") == udid:
            with _lk:
                st["n"] += 1

    @sio.on("stream:started")
    def _started(d):
        if d and d.get("udid") == udid:
            st["provider"] = d.get("provider")

    sio.connect(BASE, transports=["polling"], headers=COOKIE)
    time.sleep(4)
    with _lk:
        n_before = st["n"]
    check("MJPEG produced real frames", n_before > 0, f"{n_before} frames in 4s")

    print("\n=== 5b) SELF-HEAL: kill the MJPEG forward mid-stream ===", flush=True)
    from utils.port_utils import kill_listeners  # same-repo helper
    killed = []
    for p in range(19500, 19531):  # mjpeg forward local ports (LOCAL_PORT_START 18500 +1000)
        killed += kill_listeners(p)
    print(f"  killed MJPEG forward listener(s): {killed}", flush=True)
    with _lk:
        n_at_kill = st["n"]
    time.sleep(9)  # unhealthy-detect + 1s recovery + screenshot frames
    with _lk:
        n_after = st["n"]
    print(f"  frames: {n_at_kill} (at kill) -> {n_after} (after 9s); provider now={st['provider']}", flush=True)
    check("stream auto-healed (frames resumed after MJPEG drop)", n_after > n_at_kill,
          f"{n_at_kill} -> {n_after}")

    sio.emit("stream:stop", {"udid": udid})
    time.sleep(0.4)
    sio.disconnect()

    print("\n=== 6) disconnect cleans up ===", flush=True)
    _session().post(f"{BASE}/api/devices/{udid}/disconnect", timeout=30)
    final = next((d for d in devices() if d["udid"] == udid), {})
    check("disconnect cleared connected", final.get("connected") is False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udid", default=None)
    args = ap.parse_args()
    proc = start_backend()
    global S, COOKIE
    S, COOKIE = admin_session(BASE)
    try:
        run(args.udid)
    finally:
        teardown(proc)
    print("\n" + "=" * 56, flush=True)
    if _failures:
        print(f"RESULT: {_FAIL} — {len(_failures)} check(s) failed: {_failures}", flush=True)
        return 1
    print(f"RESULT: {_PASS} — full real-device chain works", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
