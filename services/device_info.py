"""Rich per-device info for the QuickInfo card (iOS).

Pulls lockdown public values over usbmux (no iOS-17 tunnel needed — verified:
`pymobiledevice3 lockdown info` returns ~80 keys without a tunnel) and layers on
runtime state we already track (connection, screen geometry, dev-mode, trusted).
Battery / storage are best-effort (often unreadable over plain lockdown on
iOS 17+); when missing the frontend simply hides those rings.

Mirrors the field shape of the Android project's `services/device_info.py` so the
frontend QuickInfo card is shared-shaped across platforms.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Optional

from services.command_runner import pymobiledevice3_cmd, run_command
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)

# Compact ProductType → marketing-name map (best-effort; falls back to the
# identifier). Not exhaustive — Apple ships new identifiers constantly.
_MARKETING = {
    "iPhone17,5": "iPhone 16e", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
}


def _lockdown_values(udid: str, domain: Optional[str] = None) -> dict:
    cmd = pymobiledevice3_cmd() + ["lockdown", "info", "--udid", udid]
    if domain:
        cmd += ["--domain", domain]
    try:
        res = run_command(cmd, timeout=15)
        if res.ok and res.stdout.strip():
            return json.loads(res.stdout)
    except (AppError, ValueError, json.JSONDecodeError) as exc:
        _log.debug("lockdown info%s failed for %s: %s",
                   f" [{domain}]" if domain else "", udid, exc)
    return {}


def _gather_inproc(udid: str) -> tuple[dict, dict, dict]:
    """Fetch (all_values, battery, disk) over a SINGLE in-process lockdown
    connection. ~20× faster than spawning three CLI subprocesses (each of which
    re-pays Python startup + import + a fresh lockdown handshake): all_values
    arrives with they connect, and the two domain reads are ~8ms on the same wire.
    Raises on any failure so the caller can fall back to the subprocess path.
    """
    from pymobiledevice3.exceptions import PyMobileDevice3Exception
    from pymobiledevice3.lockdown import create_using_usbmux  # already resident

    # Errors that a best-effort lockdown read/close may raise — pymobiledevice3's
    # own hierarchy plus the transport/parse errors that surface through it.
    _read_errs = (PyMobileDevice3Exception, OSError, ValueError, KeyError)

    async def _run() -> tuple[dict, dict, dict]:
        ld = await create_using_usbmux(serial=udid)
        try:
            allv = dict(ld.all_values or {})

            async def _domain(dom: str) -> dict:
                try:
                    r = ld.get_value(domain=dom)
                    r = await r if inspect.isawaitable(r) else r
                    return dict(r or {})
                except _read_errs:  # best-effort: battery/disk often unreadable
                    return {}

            return allv, await _domain("com.apple.mobile.battery"), await _domain("com.apple.disk_usage")
        finally:
            try:
                res = ld.close()
                if inspect.isawaitable(res):
                    await res
            except (PyMobileDevice3Exception, OSError):
                pass

    return asyncio.run(_run())


def _gather(udid: str) -> tuple[dict, dict, dict]:
    """Return (values, battery, disk). Prefers one in-process lockdown
    connection; on any error (event loop already running, platform quirk, …)
    falls back to the slower-but-proven per-domain subprocess CLI."""
    try:
        return _gather_inproc(udid)
    except Exception as exc:
        _log.debug("in-process lockdown failed for %s (%s); using subprocess CLI", udid, exc)
        return (
            _lockdown_values(udid),
            _lockdown_values(udid, "com.apple.mobile.battery"),
            _lockdown_values(udid, "com.apple.disk_usage"),
        )


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def collect(udid: str) -> dict:
    """Return a structured info dict for the device. Raises if not present."""
    from services.runtime_state import state  # lazy: avoid import cycle

    vals, batt, disk = _gather(udid)
    dev = state.get_device(udid)
    if not vals and not dev:
        raise AppError(ErrorCode.DEVICE_DISCONNECTED, f"No info for {udid}")

    product_type = vals.get("ProductType") or (dev.product_type if dev else "")
    info: dict = {
        "udid": udid,
        "name": vals.get("DeviceName") or (dev.name if dev else ""),
        "product_type": product_type,
        "marketing": _MARKETING.get(product_type, product_type),
        "ios_version": vals.get("ProductVersion") or (dev.ios_version if dev else ""),
        "build": vals.get("BuildVersion", ""),
        "model_number": vals.get("ModelNumber", ""),
        "hardware_model": vals.get("HardwareModel", ""),
        "cpu_arch": vals.get("CPUArchitecture", ""),
        "serial": vals.get("SerialNumber", ""),
        "activation_state": vals.get("ActivationState", ""),
        "imei": vals.get("InternationalMobileEquipmentIdentity", ""),
        "imei2": vals.get("InternationalMobileEquipmentIdentity2", ""),
        "wifi_mac": vals.get("WiFiAddress", ""),
        "bt_mac": vals.get("BluetoothAddress", ""),
        "ethernet_mac": vals.get("EthernetAddress", ""),
        "region": vals.get("RegionInfo", ""),
        "timezone": vals.get("TimeZone", ""),
        "phone_number": vals.get("PhoneNumber", ""),
        "device_class": vals.get("DeviceClass", ""),
        "connection": "usb",  # this project is USB-only
        "developer_mode": dev.developer_mode if dev else "unknown",
        "trusted": bool(dev.trusted) if dev else False,
        "wda_running": bool(dev.wda_running) if dev else False,
        "streaming": bool(dev.streaming) if dev else False,
    }
    if dev and dev.screen_width and dev.screen_height:
        info["resolution"] = {"w": dev.screen_width, "h": dev.screen_height}

    # ── best-effort metrics (hidden by the UI when absent) ──────────────
    level = _int(batt.get("BatteryCurrentCapacity"))
    if level is not None:
        info["battery"] = {"level": level, "charging": bool(batt.get("BatteryIsCharging"))}

    total = _int(disk.get("TotalDiskCapacity") or disk.get("TotalDataCapacity"))
    avail = _int(disk.get("TotalDataAvailable"))
    if total and avail is not None:
        info["storage"] = {"used": total - avail, "total": total}

    return info
