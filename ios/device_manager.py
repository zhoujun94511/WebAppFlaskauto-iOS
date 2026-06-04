"""iOS device discovery via the pymobiledevice3 CLI.

We prefer the CLI over importing pymobiledevice3 internals — the CLI surface
is far more stable across releases than the Python API. Output is JSON where
the CLI supports it; we degrade gracefully when fields/commands are absent.
"""

from __future__ import annotations

import json
from typing import List

from ios.device_models import IOSDevice
from services.command_runner import pymobiledevice3_cmd, run_command
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def _extract_json(text: str):
    """Best-effort parse of CLI stdout that may carry a leading/trailing noise
    line around a JSON array/object. Tries the whole string first, then the
    substring from the first ``[``/``{`` to its matching last bracket."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        return None
    close = "]" if text[start] == "[" else "}"
    end = text.rfind(close)
    if end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class IOSDeviceManager:
    def __init__(self, command_timeout: int = 15):
        self.command_timeout = command_timeout

    # ── availability ─────────────────────────────────────────────────
    def ensure_available(self) -> None:
        """Raise PYMOBILEDEVICE3_NOT_INSTALLED if the CLI can't be invoked."""
        try:
            res = run_command(
                pymobiledevice3_cmd() + ["version"], timeout=self.command_timeout
            )
        except AppError as exc:
            raise AppError(
                ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED,
                "pymobiledevice3 is not installed in the active environment",
                exc.detail,
            ) from exc
        if not res.ok:
            raise AppError(
                ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED,
                "pymobiledevice3 CLI returned an error on 'version'",
                {"stderr": res.stderr[-500:]},
            )

    def is_available(self) -> bool:
        try:
            self.ensure_available()
            return True
        except AppError:
            return False

    # ── discovery ────────────────────────────────────────────────────
    def scan_devices(self) -> List[IOSDevice]:
        """List connected iOS devices (usbmux). Returns [] when none attached."""
        self.ensure_available()
        # ``usbmux list`` prints a JSON array of device dicts.
        res = run_command(
            pymobiledevice3_cmd() + ["usbmux", "list"],
            timeout=self.command_timeout,
        )
        if not res.ok:
            # Distinguish "no device" from a real failure where we can.
            low = (res.stderr + res.stdout).lower()
            if "no device" in low:
                return []
            raise AppError(
                ErrorCode.NO_IOS_DEVICE,
                "Failed to list iOS devices",
                {"stderr": res.stderr[-500:], "stdout": res.stdout[-500:]},
            )
        devices = self._parse_list(res.stdout)
        return devices

    @staticmethod
    def _parse_list(stdout: str) -> List[IOSDevice]:
        stdout = (stdout or "").strip()
        if not stdout:
            return []
        data = _extract_json(stdout)
        if data is None:
            # pymobiledevice3 sometimes prefixes the JSON with a warning/progress
            # line on stdout; if even substring extraction fails, bail to empty.
            _log.warning(
                "usbmux list returned non-JSON output; cannot parse: %.200s", stdout
            )
            return []
        if isinstance(data, dict):
            data = data.get("devices", []) or list(data.values())
        out: List[IOSDevice] = []
        for entry in data or []:
            if isinstance(entry, dict):
                out.append(IOSDevice.from_pymobiledevice3_output(entry))
        return out

    # ── per-device detail ────────────────────────────────────────────
    def get_device_info(self, udid: str) -> IOSDevice:
        """Return enriched info for one device.

        ``usbmux list`` already carries the essentials (name / product /
        version / connection) and — unlike ``lockdown info`` — works without an
        iOS 17+ RemoteServiceDiscovery tunnel. So we resolve from the scan list
        and best-effort layer Developer Mode on top. Screen geometry is filled
        later from WDA's window/size on connect.
        """
        self.ensure_available()
        for dev in self.scan_devices():
            if dev.udid == udid:
                dev.developer_mode = self._developer_mode(udid)
                return dev
        raise AppError(
            ErrorCode.DEVICE_DISCONNECTED,
            f"Device {udid} not present in usbmux list",
        )

    def _developer_mode(self, udid: str) -> str:
        """Best-effort Developer Mode state; 'unknown' if not readable.

        On iOS 17+ this may require a tunnel; we never fail the request over it.
        """
        try:
            res = run_command(
                pymobiledevice3_cmd()
                + ["amfi", "developer-mode-status", "--udid", udid],
                timeout=self.command_timeout,
            )
        except AppError:
            return "unknown"
        if not res.ok:
            return "unknown"
        low = (res.stdout + res.stderr).lower()
        if "true" in low or "enabled" in low:
            return "on"
        if "false" in low or "disabled" in low:
            return "off"
        return "unknown"
