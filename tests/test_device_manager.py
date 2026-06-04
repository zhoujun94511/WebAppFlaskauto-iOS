"""Device manager: availability + usbmux list parsing, with run_command mocked."""

import json

import pytest

import ios.device_manager as dm
from ios.device_manager import IOSDeviceManager
from services.command_runner import CommandResult
from utils.app_errors import AppError, ErrorCode


def _result(rc=0, out="", err=""):
    return CommandResult(args=["x"], returncode=rc, stdout=out, stderr=err, duration_ms=1)


def test_not_installed_raises(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AppError(ErrorCode.INTERNAL_ERROR, "not found")

    monkeypatch.setattr(dm, "run_command", boom)
    mgr = IOSDeviceManager()
    with pytest.raises(AppError) as ei:
        mgr.ensure_available()
    assert ei.value.code == ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED
    assert mgr.is_available() is False


def test_scan_parses_json_list(monkeypatch):
    payload = json.dumps(
        [
            {
                "Identifier": "abc123",
                "DeviceName": "Test iPhone",
                "ProductType": "iPhone14,5",
                "ProductVersion": "17.4",
                "ConnectionType": "USB",
            }
        ]
    )

    calls = {"n": 0}

    def fake(args, **_kwargs):
        calls["n"] += 1
        if "version" in args:
            return _result(0, "4.0")
        return _result(0, payload)

    monkeypatch.setattr(dm, "run_command", fake)
    devices = IOSDeviceManager().scan_devices()
    assert len(devices) == 1
    d = devices[0]
    assert d.udid == "abc123"
    assert d.name == "Test iPhone"
    assert d.product_type == "iPhone14,5"
    assert d.ios_version == "17.4"
    # Presence in usbmux list ⇒ reachable/trusted, but NOT app-level connected.
    # `connected` only flips True after adapter.connect() brings up WDA; the UI
    # relies on this to drive the Connect button + WDA auto-launch.
    assert d.connected is False
    assert d.trusted is True


def test_scan_empty_when_no_device(monkeypatch):
    def fake(args, **_kwargs):
        if "version" in args:
            return _result(0, "4.0")
        return _result(1, "", "No device connected")

    monkeypatch.setattr(dm, "run_command", fake)
    assert IOSDeviceManager().scan_devices() == []


def test_scan_non_json_returns_empty(monkeypatch):
    def fake(args, **_kwargs):
        if "version" in args:
            return _result(0, "4.0")
        return _result(0, "some table text not json")

    monkeypatch.setattr(dm, "run_command", fake)
    assert IOSDeviceManager().scan_devices() == []
