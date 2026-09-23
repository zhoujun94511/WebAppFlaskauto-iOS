"""HEVC transport policy, framing, and MJPEG fallback. No device required."""

import time

from ios.device_models import IOSDevice
from ios.screen_transport.base import BaseScreenTransport, EncodedPacket
from services.encoded_stream import EncodedStreamSession
from services.ios_file_service import media_afc_path
from ios.ios_adapter import IOSAdapter
from ios.screen_transport.framing import frame_access_unit
from ios.screen_transport.hevc_rsd import HevcRsdTransport, build_rtcp_pli, build_rtcp_rr
from ios.screen_transport.policy import hevc_supported_ios, resolve_screen_provider
from services.ios_stream_service import IOSStreamService
from services.runtime_state import state
from utils.app_errors import AppError, ErrorCode


def test_resolve_wda_bundle_prefers_the_installed_runner():
    configured = "com.facebook.WebDriverAgentRunner.crts.test.xctrunner"
    installed = configured + ".xctrunner"
    assert IOSAdapter.resolve_wda_bundle(configured, [installed]) == installed
    assert IOSAdapter.resolve_wda_bundle(configured, [configured]) == configured
    assert IOSAdapter.resolve_wda_bundle(configured, ["com.example.app"]) == configured


def test_media_afc_path_keeps_directory_prefixes():
    assert media_afc_path(".") == "/"
    assert media_afc_path("DCIM/100APPLE") == "/DCIM/100APPLE"
    assert media_afc_path("/DCIM/100APPLE/") == "/DCIM/100APPLE"


def test_hevc_version_gate():
    assert hevc_supported_ios("27.0") is True
    assert hevc_supported_ios("27.0.1") is True
    assert hevc_supported_ios("18.6.2") is False
    assert hevc_supported_ios("26.0.1") is False
    assert hevc_supported_ios("17.4") is False
    assert hevc_supported_ios("16.7") is False
    assert hevc_supported_ios("") is False


def test_resolve_auto_and_explicit():
    assert resolve_screen_provider("auto", "27.0") == "hevc"
    assert resolve_screen_provider("auto", "18.6.2") == "mjpeg"
    assert resolve_screen_provider("hevc", "18.6.2") == "mjpeg"
    assert resolve_screen_provider("screenshot", "18.0") == "screenshot"
    assert resolve_screen_provider(None, "18.0", default="mjpeg") == "mjpeg"
    assert resolve_screen_provider("nope", "18.0") == "mjpeg"


def test_rtcp_feedback_packets_are_well_formed():
    pli = build_rtcp_pli(1, 2)
    rr = build_rtcp_rr(1, 2, 10)
    assert len(pli) == 12
    assert len(rr) == 32 + 12


def test_frame_access_unit_layout():
    nal = b"\x00\x01"
    framed = frame_access_unit([nal], key=True)
    assert framed[4] == 0
    assert int.from_bytes(framed[:4], "big") == 1 + 4 + len(nal)
    assert frame_access_unit([nal], key=False)[4] == 1
    assert frame_access_unit([nal], key=True, reset=True)[4] == 2


class _Jpeg:
    name = "mjpeg"

    def __init__(self):
        self.stopped = False

    @staticmethod
    def start():
        return None

    def stop(self):
        self.stopped = True

    def read_frame(self):
        time.sleep(0.02)
        if self.stopped:
            return None
        return b"\xff\xd8\xff\xd9"

    def health(self):
        return not self.stopped

    @staticmethod
    def get_frame_size():
        return 2, 2


class _Adapter:
    def __init__(self):
        self.jpeg = _Jpeg()
        self.transport_calls = 0

    @staticmethod
    def check_wda(_udid):
        return True

    def connect(self, udid):
        raise AssertionError(f"connect should not run when WDA is up ({udid})")

    def make_screen_transport(self, _udid):
        self.transport_calls += 1
        raise AppError(ErrorCode.SCREEN_PROVIDER_FAILED, "no hevc in this test")

    def make_screen_provider(self, _udid, name, **_kwargs):
        assert name == "mjpeg"
        return self.jpeg

    def fallback_provider(self, _udid, **_kwargs):
        raise AssertionError("hevc failure must fall back to mjpeg, not screenshot")


def test_hevc_open_failure_falls_back_to_mjpeg(monkeypatch):
    udid = "udid-hevc-fallback"
    state.devices[udid] = IOSDevice(
        udid=udid, ios_version="27.0", connected=True, local_wda_port=18100
    )
    adapter = _Adapter()
    monkeypatch.setattr("services.ios_stream_service.get_adapter", lambda: adapter)
    svc = IOSStreamService({"IOS_SCREEN_PROVIDER": "mjpeg", "IOS_STREAM_MAX_RECOVERY": 1})
    try:
        status = svc.start_stream(udid, provider="auto")
        assert adapter.transport_calls == 1
        assert status["provider"] == "mjpeg"
        assert status["running"] is True
    finally:
        svc.stop_stream(udid)
        state.devices.pop(udid, None)
        state.streams.pop(udid, None)


def test_request_keyframe_is_safe_without_a_stream():
    IOSStreamService({}).request_keyframe("missing-device")
    assert True


class _DyingTransport(BaseScreenTransport):
    name = "hevc"

    def __init__(self):
        self.stopped = False

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True

    def read_packet(self):
        return None

    def health(self) -> bool:
        return False

    def request_keyframe(self) -> None:
        return None


def test_unhealthy_hevc_releases_the_tunnel_then_uses_mjpeg(monkeypatch):
    udid = "udid-hevc-release"
    transport = _DyingTransport()
    state.devices[udid] = IOSDevice(
        udid=udid, ios_version="27.0", connected=True, local_wda_port=18100
    )
    state.streams[udid] = EncodedStreamSession(udid, transport)
    adapter = _Adapter()
    monkeypatch.setattr("services.ios_stream_service.get_adapter", lambda: adapter)
    svc = IOSStreamService({"IOS_SCREEN_PROVIDER": "mjpeg", "IOS_STREAM_MAX_RECOVERY": 1})
    try:
        svc._on_unhealthy(udid)
        assert transport.stopped is True
        deadline = time.time() + 4
        while time.time() < deadline:
            current = state.streams.get(udid)
            if current is not None and current.provider_name == "mjpeg":
                break
            time.sleep(0.05)
        assert state.streams[udid].provider_name == "mjpeg"
    finally:
        svc.stop_stream(udid)
        state.devices.pop(udid, None)
        state.streams.pop(udid, None)


def test_full_hevc_queue_drop_asks_for_a_keyframe():
    transport = HevcRsdTransport("udid-queue")
    delta = EncodedPacket(codec="hevc", keyframe=False, data=b"\x00\x00\x00\x01\x01")
    for _ in range(45):
        transport._queue.put_nowait(delta)
    transport._enqueue(EncodedPacket(codec="hevc", keyframe=False, data=b"\x00\x00\x00\x01\x01"))
    assert transport._want_key is True
    assert transport._queue.qsize() == 45

    transport._want_key = False
    transport._enqueue(
        EncodedPacket(
            codec="hevc",
            keyframe=True,
            data=frame_access_unit([b"\x28"], key=True),
        )
    )
    found = None
    while not transport._queue.empty():
        item = transport._queue.get_nowait()
        if item.keyframe:
            found = item
    assert found is not None and found.reset is True and found.data[4] == 2
