"""Phase-1 check: HEVC over pymobiledevice3 userspace RSD while WDA still answers.

Does not start Flask. Success (exit 0) means, on one USB device:

  * a few seconds of RTP/HEVC were captured, and
  * an optional WDA endpoint still accepted /status and a tap during capture, and
  * go-ios agent ports that were listening before the capture are still listening.

Exit 2 means the check could not be run (no device, or this interpreter's
pymobiledevice3 has no CoreDevice HEVC API). Exit 1 means it ran and the
channels did not coexist.

WDA is probed only when --wda-url is set (the local usbmux forward, e.g.
http://127.0.0.1:18100). go-ios ports default to 60105 and 28100.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# Pin recorded once the API was confirmed present in this release.
# Coexistence on a real phone is a separate, device-side result below.
PMD3_HEVC_RELEASE = "11.17.0"


def _port_open(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _hevc_api_available() -> bool:
    """Probe pymobiledevice3 only. Do not import the Flask ``ios`` package."""
    try:
        from pymobiledevice3.remote.core_device.screen_stream import (  # noqa: F401
            capture_rtp_to_file,
        )
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel  # noqa: F401
    except ImportError:
        return False
    return True


def _installed_version() -> str:
    import importlib.metadata as md

    try:
        return md.version("pymobiledevice3")
    except md.PackageNotFoundError:
        return ""


def _list_udids() -> list[str]:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pymobiledevice3", "usbmux", "list"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    text = (proc.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    udids = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                udid = item.get("Identifier") or item.get("UniqueDeviceID") or item.get("udid")
                if udid:
                    udids.append(str(udid))
    return udids


def _wda_tap(base: str) -> str:
    """GET /status, open a session, tap the middle of a nominal screen."""
    status = urllib.request.urlopen(base.rstrip("/") + "/status", timeout=5)
    status.read()
    body = json.dumps(
        {"capabilities": {"alwaysMatch": {"shouldWaitForQuiescence": False}, "firstMatch": [{}]}}
    ).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/session",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    sid = payload.get("sessionId") or (payload.get("value") or {}).get("sessionId")
    if not sid:
        return "status ok, session missing"
    actions = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": 200, "y": 400},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": 40},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        ]
    }
    tap = urllib.request.Request(
        f"{base.rstrip('/')}/session/{sid}/actions",
        data=json.dumps(actions).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(tap, timeout=8) as resp:
        resp.read()
    return "status+tap ok"


async def _capture(udid: str, output: Path, seconds: float) -> int:
    from pymobiledevice3.remote.core_device.screen_stream import capture_rtp_to_file
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

    output.parent.mkdir(parents=True, exist_ok=True)
    tunnel = UserspaceRsdTunnel(serial=udid, remotepairing_fallback=False)
    rsd = await tunnel.aopen()
    try:
        return await capture_rtp_to_file(rsd, output, duration=seconds)
    finally:
        await tunnel.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="HEVC + WDA coexistence spike")
    parser.add_argument("--udid", default="")
    parser.add_argument("--wda-url", default="")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--goios-port", type=int, action="append", default=[])
    args = parser.parse_args()
    ports = args.goios_port or [60105, 28100]

    report = {
        "pmd3_required": PMD3_HEVC_RELEASE,
        "pmd3_installed": _installed_version(),
        "hevc_api": _hevc_api_available(),
        "udid": args.udid,
        "packets": 0,
        "wda": "not probed",
        "goios_ports_before": {},
        "goios_ports_after": {},
        "verdict": "not-run",
    }

    if not report["hevc_api"]:
        report["verdict"] = "not-run: pymobiledevice3 has no CoreDevice HEVC API"
        print(json.dumps(report, indent=2))
        return 2

    udids = _list_udids()
    udid = args.udid or (udids[0] if udids else "")
    report["udid"] = udid
    if not udid:
        report["verdict"] = "not-run: no usbmux device"
        print(json.dumps(report, indent=2))
        return 2

    before = {str(p): _port_open(p) for p in ports}
    report["goios_ports_before"] = before
    output = Path(args.output) if args.output else Path.cwd() / "hevc-coexist.rtp"
    wda_result = {"text": "not probed"}
    usbmux_during: dict[str, bool | str | None] = {"present": None}

    def _during() -> None:
        # usbmux must still enumerate the phone while HEVC is running. That is
        # the check that this tunnel did not claim the whole USB device.
        try:
            usbmux_during["present"] = udid in _list_udids()
        except Exception as lookup_error:  # noqa: BLE001
            usbmux_during["present"] = f"failed: {lookup_error}"
        if not args.wda_url:
            return
        try:
            wda_result["text"] = _wda_tap(args.wda_url)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as tap_error:
            wda_result["text"] = f"failed: {tap_error}"

    timer = threading.Timer(1.0, _during)
    timer.daemon = True
    timer.start()
    try:
        packets = asyncio.run(_capture(udid, output, args.seconds))
    except Exception as exc:  # noqa: BLE001 — this spike reports, it does not raise
        hint = ""
        if "displayservice" in str(exc):
            hint = " (developer image is probably not mounted; mounter auto-mount first)"
        report["verdict"] = f"failed: {exc}{hint}"
        report["goios_ports_after"] = {str(p): _port_open(p) for p in ports}
        report["usbmux_during"] = usbmux_during["present"]
        print(json.dumps(report, indent=2))
        return 1
    finally:
        timer.cancel()

    report["packets"] = int(packets or 0)
    report["output"] = str(output)
    report["wda"] = wda_result["text"]
    report["usbmux_during"] = usbmux_during["present"]
    if usbmux_during["present"] is not True:
        report["verdict"] = "failed: device disappeared from usbmux during HEVC"
        print(json.dumps(report, indent=2))
        return 1
    after = {str(p): _port_open(p) for p in ports}
    report["goios_ports_after"] = after
    killed = [p for p, was in before.items() if was and not after.get(p)]
    if killed:
        report["verdict"] = f"failed: go-ios agent port closed during HEVC ({', '.join(killed)})"
        print(json.dumps(report, indent=2))
        return 1
    if report["packets"] <= 0:
        report["verdict"] = "failed: no RTP packets"
        print(json.dumps(report, indent=2))
        return 1
    if args.wda_url and not str(report["wda"]).endswith("ok"):
        report["verdict"] = "failed: WDA did not answer during HEVC"
        print(json.dumps(report, indent=2))
        return 1
    if not args.wda_url:
        report["verdict"] = "hevc-ok-wda-not-probed"
        print(json.dumps(report, indent=2))
        return 2
    report["verdict"] = "coexist-ok"
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
