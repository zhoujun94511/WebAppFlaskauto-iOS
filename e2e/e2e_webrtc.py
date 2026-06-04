"""WebRTC real-device test (zero mock): boots a backend with WebRTC enabled,
negotiates a PeerConnection from a headless aiortc client, and asserts decoded
video frames arrive off a real device. Skips if no device attached.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # project root for utils./app imports

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import requests
import socketio
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from e2e_common import admin_session

ROOT = Path(__file__).resolve().parent.parent
PORT, TUNNEL_PORT = 5096, 28296
BASE = f"http://127.0.0.1:{PORT}"

S: requests.Session | None = None  # logged-in admin session (auth always on)
COOKIE: dict = {}


def _session() -> requests.Session:
    return cast(requests.Session, S)


def _negotiated_video_codec(sdp: str) -> str:
    """Return the rtpmap name of the first payload type on the m=video line."""
    lines = sdp.splitlines()
    mvideo = next((l for l in lines if l.startswith("m=video")), "")
    pts = mvideo.split()[3:] if mvideo else []
    if not pts:
        return "?"
    pt = pts[0]
    rtpmap = next((l for l in lines if l.startswith(f"a=rtpmap:{pt} ")), "")
    return rtpmap.split(" ", 1)[1].split("/")[0] if rtpmap else "?"


def start_backend() -> subprocess.Popen:
    env = os.environ.copy()
    env.update(PORT=str(PORT), HOST="127.0.0.1", OPEN_BROWSER="0",
               IOS_GOIOS_TUNNEL_INFO_PORT=str(TUNNEL_PORT), IOS_LOCAL_PORT_START="18200",
               IOS_ENABLE_WEBRTC="1")
    print(f"[boot] backend on :{PORT} (WebRTC on)…", flush=True)
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
    from contextlib import suppress
    with suppress(Exception):
        from utils.port_utils import kill_listeners
        kill_listeners(PORT); kill_listeners(TUNNEL_PORT)


async def negotiate() -> int:
    h = _session().get(BASE + "/api/health", timeout=10).json()["data"]
    assert h.get("webrtc_enabled") is True, "health must report webrtc_enabled"
    devs = _session().get(BASE + "/api/devices?rescan=1", timeout=30).json()["data"]["devices"]
    if not devs:
        print("  SKIP: no device", flush=True); return -1
    # Pick the first device whose WDA actually comes up (skip ones with a
    # broken/missing WDA runner — common on brand-new iOS where WDA needs a
    # rebuild). The product returns a clean webrtc:error for those.
    udid = None
    for d in devs:
        r = _session().post(f"{BASE}/api/devices/{d['udid']}/connect", timeout=120).json()
        if (r.get("data") or {}).get("device", {}).get("wda_running"):
            udid = d["udid"]; break
        print(f"  skip {d['udid'][:12]} (WDA not up)", flush=True)
    if not udid:
        print("  SKIP: no device with a working WDA", flush=True); return -1
    print(f"  using {udid[:12]}", flush=True)

    sio = socketio.AsyncClient()
    pc = RTCPeerConnection()
    frames = {"n": 0}
    done = asyncio.Event()
    ans = {}

    @sio.on("webrtc:answer")
    async def _a(payload): ans["ok"] = payload; done.set()

    @sio.on("webrtc:error")
    async def _e(payload): ans["err"] = payload; print("  webrtc:error", payload, flush=True); done.set()

    @pc.on("track")
    def _t(track):
        async def pump():
            while True:
                try:
                    await track.recv(); frames["n"] += 1
                except MediaStreamError:
                    break
        asyncio.ensure_future(pump())

    await sio.connect(BASE, transports=["polling"], headers=COOKIE)
    pc.addTransceiver("video", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)
    await sio.emit("webrtc:offer",
                   {"udid": udid, "sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    await asyncio.wait_for(done.wait(), timeout=60)
    if "ok" not in ans:
        return 0
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["ok"]["sdp"], type=ans["ok"]["type"]))
    globals()["_CODEC"] = _negotiated_video_codec(ans["ok"]["sdp"])
    print(f"  negotiated video codec: {globals()['_CODEC']}", flush=True)
    await asyncio.sleep(4)
    n1 = frames["n"]
    print(f"  baseline: {n1} frames in 4s", flush=True)

    # mid-stream self-heal: kill the MJPEG forward; frames must keep flowing
    # (track swaps to screenshot, PeerConnection stays up).
    from utils.port_utils import kill_listeners
    killed = []
    for p in range(19200, 19231):  # LOCAL_PORT_START 18200 + 1000
        killed += kill_listeners(p)
    print(f"  killed MJPEG forward(s): {killed}", flush=True)
    n_kill = frames["n"]
    await asyncio.sleep(9)
    n2 = frames["n"]
    print(f"  after self-heal: {n_kill} -> {n2} (frames kept flowing: {n2 > n_kill})", flush=True)

    await sio.emit("webrtc:stop", {"udid": udid})
    await asyncio.sleep(0.3)
    await pc.close()
    await sio.disconnect()
    globals()["_HEALED"] = n2 > n_kill
    return n1


def main() -> int:
    p = start_backend()
    global S, COOKIE
    S, COOKIE = admin_session(BASE)
    try:
        n = asyncio.run(negotiate())
    finally:
        teardown(p)
    if n < 0:
        print("RESULT: SKIP (no device)"); return 0
    healed = globals().get("_HEALED", False)
    codec = globals().get("_CODEC", "?")
    is_h264 = "H264" in codec.upper()
    ok = n > 0 and healed and is_h264
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} — {n} frames; codec={codec} "
          f"(H264={'OK' if is_h264 else 'NO'}); self-heal={'OK' if healed else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
