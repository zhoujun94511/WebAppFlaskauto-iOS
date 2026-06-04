"""Port forward: free-port allocation + pymobiledevice3-missing mapping."""

import pytest

import ios.port_forward as pf
from ios.port_forward import IOSPortForward
from utils.app_errors import AppError, ErrorCode
from utils.port_utils import find_free_port, is_port_free


def test_find_free_port_returns_open_port():
    port = find_free_port(28100, 28200)
    assert is_port_free(port)


def test_start_forward_maps_missing_binary(monkeypatch):
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("python -m pymobiledevice3 not found")

    monkeypatch.setattr(pf.subprocess, "Popen", boom)
    fwd = IOSPortForward(local_port_start=28300)
    with pytest.raises(AppError) as ei:
        fwd.start_forward("udid-x")
    assert ei.value.code == ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED


def test_start_forward_detects_early_exit(monkeypatch):
    class FakeProc:
        def __init__(self):
            self._polls = 0
            self.stderr = _Bytes(b"forward failed: connection refused")

        @staticmethod
        def poll():
            return 1  # already exited

        def terminate(self):
            pass

        @staticmethod
        def wait(*_args, **_kwargs):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(pf.subprocess, "Popen", lambda *_args, **_kwargs: FakeProc())
    # is_port_open should be False since nothing listens
    monkeypatch.setattr(pf, "is_port_open", lambda *_args, **_kwargs: False)
    fwd = IOSPortForward(local_port_start=28400)
    with pytest.raises(AppError) as ei:
        fwd.start_forward("udid-y")
    assert ei.value.code in (
        ErrorCode.PORT_FORWARD_FAILED,
        ErrorCode.IOS17_TUNNEL_FAILED,
    )


class _Bytes:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data
