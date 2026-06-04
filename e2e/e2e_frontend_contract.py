"""前后端联调 —— 以「真实前端的视角」打通整条契约链路（真机）。

不同于 e2e_smoke（用 admin 绕过预约网关），本脚本完全模拟 Vue 前端里一个
**普通用户**的操作序列，校验前端 http.js / useAuth / useReservations /
useStream / useControl 依赖的每一个响应信封与 socket 事件：

    0. SPA 资源    -> 后端把 frontend/dist 的 index.html + JS/CSS 真正发出来
    1. 注册/登录   -> cookie 会话；check-auth 返回 authenticated + role=user
    2. 设备列表    -> 统一信封 data.devices 为数组
    3. 预约占用    -> POST /api/reservations 占用成功，is_mine=true
    4. 占用排他    -> 第二个用户占用同机被拒(409)，控制被拒(403 RESERVATION_DENIED)
    5. 连接        -> 占用者 connect 成功（起 WDA）
    6. 实时流      -> 占用者 socket 开流，真 MJPEG 帧 > 0
    7. 控制        -> 占用者截图/控制 200
    8. 释放        -> DELETE 预约 + 断开，状态清干净

复用「已在运行」的后端（默认 127.0.0.1:5099），不自起后端、不碰你的 5001。
    ../.venv/Scripts/python.exe e2e_frontend_contract.py [--base http://127.0.0.1:5099] [--udid <UDID>]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

import requests
import socketio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PASS, _FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{_PASS if cond else _FAIL}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        _failures.append(name)


def _ok(r: requests.Response) -> dict:
    try:
        return r.json()
    except Exception:
        return {}


def register(base: str, s: requests.Session, username: str, pw: str) -> None:
    # Idempotent: CONFLICT just means the account already exists from a prior run.
    r = s.post(base + "/api/auth/register",
               json={"username": username, "email": f"{username}@local.test", "password": pw}, timeout=15)
    body = _ok(r)
    if not body.get("success") and body.get("code") != "CONFLICT":
        raise SystemExit(f"[contract] register {username} failed: {r.status_code} {r.text[:160]}")


def login(base: str, username: str, pw: str) -> tuple[requests.Session, dict]:
    s = requests.Session()
    r = s.post(base + "/api/auth/login", json={"username": username, "password": pw}, timeout=15)
    if not _ok(r).get("success"):
        raise SystemExit(f"[contract] login {username} failed: {r.status_code} {r.text[:160]}")
    cookie = s.cookies.get("session")
    return s, ({"Cookie": f"session={cookie}"} if cookie else {})


def run(base: str, udid_arg: str | None) -> None:
    print("\n=== 0) SPA assets served by backend ===", flush=True)
    root = requests.get(base + "/", timeout=15)
    html = root.text
    check("GET / -> 200 html", root.status_code == 200 and "<div id=\"app\">" in html, f"HTTP {root.status_code}")
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    check("index.html references built assets", len(assets) >= 2, f"{len(assets)} refs")
    for a in assets[:4]:
        ar = requests.get(base + a, timeout=15)
        check(f"asset {a.split('/')[-1]} -> 200", ar.status_code == 200, f"HTTP {ar.status_code}")

    print("\n=== 1) register + login (normal user) ===", flush=True)
    register(base, requests.Session(), "uitester", "uitester123")
    register(base, requests.Session(), "uitester2", "uitester123")
    s, cookie = login(base, "uitester", "uitester123")
    ca = _ok(s.get(base + "/api/auth/check-auth", timeout=15))
    check("check-auth authenticated", ca.get("data", {}).get("authenticated") is True)
    check("role is plain user", (ca.get("data", {}).get("user") or {}).get("role") == "user",
          (ca.get("data", {}).get("user") or {}).get("role"))

    print("\n=== 2) device list (unified envelope) ===", flush=True)
    dl = _ok(s.get(base + "/api/devices?rescan=1", timeout=30))
    check("devices envelope ok", dl.get("success") is True)
    devs = dl.get("data", {}).get("devices", [])
    check("devices is a list", isinstance(devs, list), f"{len(devs)} device(s)")
    if not devs:
        return
    udid = udid_arg or devs[0]["udid"]
    check("target present in list", any(d["udid"] == udid for d in devs), udid[:12])

    print("\n=== 3) reservation claim (useReservations) ===", flush=True)
    rv = _ok(s.post(base + "/api/reservations", json={"device_id": udid, "minutes": 5}, timeout=15))
    check("claim success", rv.get("success") is True, rv.get("message"))
    check("reservation is_mine", (rv.get("data", {}).get("reservation") or {}).get("is_mine") is True)
    lst = _ok(s.get(base + "/api/reservations", timeout=15))
    mine = [r for r in lst.get("data", {}).get("reservations", []) if r["device_id"] == udid]
    check("reservation shows in list as mine", bool(mine) and mine[0]["is_mine"] is True)

    print("\n=== 4) exclusivity: a second user is locked out ===", flush=True)
    s2, cookie2 = login(base, "uitester2", "uitester123")
    claim2 = s2.post(base + "/api/reservations", json={"device_id": udid, "minutes": 5}, timeout=15)
    check("2nd user claim rejected (409)", claim2.status_code == 409,
          f"HTTP {claim2.status_code} {_ok(claim2).get('code')}")
    ctrl2 = s2.post(base + f"/api/devices/{udid}/screenshot", timeout=20)
    check("2nd user control denied (403 RESERVATION_DENIED)",
          ctrl2.status_code == 403 and _ok(ctrl2).get("code") == "RESERVATION_DENIED",
          f"HTTP {ctrl2.status_code} {_ok(ctrl2).get('code')}")

    print("\n=== 5) owner connect (auto WDA) ===", flush=True)
    t0 = time.time()
    cn = _ok(s.post(base + f"/api/devices/{udid}/connect", timeout=150))
    dev = (cn.get("data") or {}).get("device") or {}
    check("connect success", cn.get("success") is True, f"{time.time()-t0:.1f}s")
    check("device connected", dev.get("connected") is True)
    check("WDA running", dev.get("wda_running") is True)

    print("\n=== 6) owner live stream over socket (useStream) ===", flush=True)
    st = {"n": 0, "provider": None, "started": False}
    lk = threading.Lock()
    sio = socketio.Client()

    @sio.event
    def connect():
        sio.emit("stream:start", {"udid": udid, "provider": "mjpeg", "fps": 12})

    @sio.on("stream:started")
    def _started(d):
        if d and d.get("udid") == udid:
            with lk:
                st["started"] = True
                st["provider"] = d.get("provider")

    @sio.on("stream:frame")
    def _frame(d):
        if d and d.get("udid") == udid:
            with lk:
                st["n"] += 1

    sio.connect(base, transports=["polling"], headers=cookie)
    time.sleep(4)
    with lk:
        n, started, provider = st["n"], st["started"], st["provider"]
    check("stream:started ack received", started is True, f"provider={provider}")
    check("real MJPEG frames over socket", n > 0, f"{n} frames in 4s")

    print("\n=== 7) owner control (useControl) ===", flush=True)
    sc = s.post(base + f"/api/devices/{udid}/screenshot", timeout=20)
    check("owner screenshot -> 200", sc.status_code == 200, f"HTTP {sc.status_code}")
    # Exactly the payload useControl.tap() sends: {x, y, display_width, display_height}.
    tp = s.post(base + f"/api/devices/{udid}/tap",
                json={"x": 100, "y": 200, "display_width": 200, "display_height": 433},
                timeout=20)
    check("owner tap -> 200", tp.status_code == 200, f"HTTP {tp.status_code}")

    print("\n=== 8) release + disconnect (teardown) ===", flush=True)
    sio.emit("stream:stop", {"udid": udid})
    time.sleep(0.4)
    sio.disconnect()
    rel = _ok(s.delete(base + f"/api/reservations/{udid}", timeout=15))
    check("release success", rel.get("success") is True)
    s.post(base + f"/api/devices/{udid}/disconnect", timeout=20)
    after = _ok(s.get(base + "/api/devices?rescan=1", timeout=30))
    fin = next((d for d in after.get("data", {}).get("devices", []) if d["udid"] == udid), {})
    check("disconnect cleared connected", fin.get("connected") is False)
    # post-release the reservation must be gone for everyone
    relist = _ok(s.get(base + "/api/reservations", timeout=15))
    check("reservation cleared after release",
          all(r["device_id"] != udid for r in relist.get("data", {}).get("reservations", [])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("CONTRACT_BASE", "http://127.0.0.1:5099"))
    ap.add_argument("--udid", default=None)
    args = ap.parse_args()
    print(f"[contract] target backend: {args.base}", flush=True)
    try:
        requests.get(args.base + "/api/health", timeout=5)
    except requests.RequestException:
        raise SystemExit(f"[contract] no backend at {args.base} — start it first")
    run(args.base, args.udid)
    print("\n" + "=" * 56, flush=True)
    if _failures:
        print(f"RESULT: {_FAIL} — {len(_failures)} check(s) failed: {_failures}", flush=True)
        return 1
    print(f"RESULT: {_PASS} — frontend<->backend contract works end-to-end", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
