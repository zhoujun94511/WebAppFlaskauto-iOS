"""IOSAdapter — the single platform seam the services talk to.

When Android is folded in later, there'll be an AndroidAdapter exposing the
same surface (discover / connect / control / screen provider), and the
services become platform-agnostic. Keeping all iOS specifics behind this
class is what makes that merge cheap.
"""

from __future__ import annotations

import os
import threading
import subprocess
from contextlib import suppress
from typing import List, Optional

from ios.device_manager import IOSDeviceManager
from ios.device_models import IOSDevice
from ios.port_forward import IOSPortForward
from ios.screen_provider.base_provider import BaseScreenProvider
from ios.screen_provider.wda_mjpeg import WdaMjpegProvider
from ios.screen_provider.wda_screenshot import WdaScreenshotProvider
from ios.wda_controller import WDAController
from ios.wda_launcher import WDALauncher
from ios.go_ios import GoIOS
from ios.tunnel_manager import TunnelManager
from services.runtime_state import state
from services.webrtc_bridge import WebRTCBridge
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class IOSAdapter:
    platform = "ios"

    def __init__(self, config: dict):
        self.config = config
        self.device_manager = IOSDeviceManager(
            command_timeout=int(config.get("IOS_COMMAND_TIMEOUT", 15))
        )
        self.port_forward = IOSPortForward(
            local_port_start=int(config.get("IOS_LOCAL_PORT_START", 18100))
        )
        self.wda_remote_port = int(config.get("IOS_WDA_REMOTE_PORT", 8100))
        self.wda_timeout = float(config.get("IOS_WDA_TIMEOUT", 15))
        self.auto_launch_wda = str(config.get("IOS_AUTO_LAUNCH_WDA", "0")) == "1"
        self.wda_launcher = WDALauncher(
            bundle_id=config.get(
                "IOS_WDA_BUNDLE_ID",
                "com.facebook.WebDriverAgentRunner.xctrunner",
            ),
            launch_timeout=float(config.get("IOS_WDA_LAUNCH_TIMEOUT", 40)),
            # USE_PORT opens WDA's control server (8100); MJPEG_SERVER_PORT
            # opens its MJPEG broadcaster (9100). Without the latter the build
            # never binds 9100 and the stream falls back to screenshots.
            env={
                "USE_PORT": str(self.wda_remote_port),
                "MJPEG_SERVER_PORT": str(int(config.get("IOS_MJPEG_REMOTE_PORT", 9100))),
            },
        )
        # go-ios engine (no-admin userspace tunnel + runwda) — fills the one
        # gap pymobiledevice3 has on Windows/iOS 17+. Optional + degrades.
        self.use_goios = str(config.get("IOS_USE_GOIOS", "1")) == "1"
        self.goios = GoIOS(
            config.get("IOS_GOIOS_BIN") or None,
            prefer_system=str(config.get("IOS_GOIOS_PREFER_SYSTEM", "0")) == "1",
            tunnel_info_port=int(config.get("IOS_GOIOS_TUNNEL_INFO_PORT", 28100)),
        )
        self.tunnel = TunnelManager(self.goios)
        self._goios_tunnel_enabled = str(config.get("IOS_GOIOS_TUNNEL", "1")) == "1"
        self._wda_procs: dict[str, subprocess.Popen] = {}  # udid -> go-ios runwda Popen
        self._ddi_error: dict[str, str] = {}  # udid -> last DDI-mount failure reason (or absent)
        # WebRTC transport (optional, IOS_ENABLE_WEBRTC). Reuses the JPEG
        # ScreenProviders as its video source.
        self.webrtc = WebRTCBridge(config)
        self._lock = threading.RLock()

    # ── discovery ────────────────────────────────────────────────────
    def is_backend_available(self) -> bool:
        return self.device_manager.is_available()

    def discover(self) -> List[IOSDevice]:
        scanned = self.device_manager.scan_devices()
        present = set()
        for dev in scanned:
            present.add(dev.udid)
            state.upsert_device(dev)
        # Devices that vanished from the scan were unplugged: drop them and tear
        # down our side (forwards + WDA), otherwise we'd keep relaunching WDA for
        # a device that's "not found" and leave a ghost card in the grid.
        for udid in state.remove_missing(present):
            _log.info("device unplugged, tearing down: %s", udid)
            with suppress(Exception):
                self.disconnect(udid)
        return state.list_devices()

    def refresh_device(self, udid: str) -> IOSDevice:
        info = self.device_manager.get_device_info(udid)
        return state.upsert_device(info)

    # ── connection / WDA ─────────────────────────────────────────────
    def connect(self, udid: str) -> IOSDevice:
        device = state.get_device(udid)
        if not device:
            # Try a fresh scan before giving up.
            self.discover()
            device = state.get_device(udid)
        if not device:
            raise AppError(ErrorCode.NO_IOS_DEVICE, f"Device {udid} not found")

        handle = self.port_forward.start_forward(
            udid, remote_port=self.wda_remote_port
        )
        device.local_wda_port = handle.local_port

        controller = self._controller(udid, handle.local_port)
        if not controller.health_check():
            self._bring_up_wda(udid, controller)
            if not controller.health_check():
                out = self._wda_launch_output(udid)
                if out:
                    _log.warning("runwda output for %s:\n%s", udid, out)
                msg = (
                    "WebDriverAgent did not answer /status. Either start the WDA "
                    "runner on the device, or enable auto-launch (go-ios is "
                    "bundled for a no-admin launch on iOS 17+)."
                )
                if out:
                    # Surface the launcher's own reason (signing expired, device
                    # locked, runner not installed/trusted, wrong bundle id, …).
                    msg += f"\nrunwda: {out[-300:]}"
                # Supplementary hint only — a DDI-mount problem is a likely cause,
                # but NOT a hard gate (WDA can still run if a DDI was mounted by
                # Xcode/another tool). go-ios's own `image auto` matches the DDI
                # per device/version; we don't override its result.
                ddi_err = self._ddi_error.get(udid)
                if ddi_err:
                    msg += "\n提示:开发者磁盘镜像(DDI)自动挂载未成功,若仍无法连接可更新 go-ios 或用 Xcode 挂载对应 DDI。"
                raise AppError(
                    ErrorCode.WDA_NOT_RUNNING, msg,
                    {"local_port": handle.local_port, "runwda_output": out[-600:], "ddi_error": ddi_err or ""},
                )
        controller.create_session(force=True)  # WDA may have restarted → new session
        # Enable/tune WDA's MJPEG broadcaster (device 9100) — best-effort.
        try:
            controller.update_settings(
                {
                    "mjpegServerFramerate": int(self.config.get("IOS_MJPEG_FRAMERATE", 30)),
                    "mjpegServerScreenshotQuality": int(self.config.get("IOS_MJPEG_QUALITY", 60)),
                    "mjpegScalingFactor": int(self.config.get("IOS_MJPEG_SCALING", 100)),
                    # Don't pause screen capture / control waiting for the UI to go
                    # idle — main fix for frame-drops during a swipe (WDA otherwise
                    # stalls while the scroll animation settles).
                    "waitForIdleTimeout": 0,
                    "animationCoolOffTimeout": 0,
                    # Lighter element payloads → less per-request WDA overhead.
                    "shouldUseCompactResponses": True,
                }
            )
        except AppError as exc:
            _log.info("MJPEG settings not applied (will use screenshot fallback): %s", exc.code)
        device.wda_running = True
        device.connected = True
        state.touch(udid)  # reset idle clock on (re)connect
        try:
            w, h = controller.get_window_size()
            if w and h:
                device.screen_width, device.screen_height = w, h
            device.orientation = controller.get_orientation()
        except AppError:
            pass
        return device

    def _mount_ddi(self, udid: str) -> tuple[bool, str]:
        """Mount the Developer Disk Image — needed for iOS 17+ xctest/WDA.

        PRIMARY: pymobiledevice3 ``mounter auto-mount`` — fetches the TSS
        personalized image from Apple, no-admin, and is version-adaptive, so it
        handles new iOS (e.g. 26.x) that go-ios's static image catalog resolves
        wrong. FALLBACK: go-ios ``image auto``. Best-effort, never raises.
        """
        from services.command_runner import pymobiledevice3_cmd, run_command

        try:
            res = run_command(pymobiledevice3_cmd() + ["mounter", "auto-mount", "--udid", udid], timeout=180)
            blob = f"{res.stdout}\n{res.stderr}".lower()
            if res.ok or "already" in blob or "mounted successfully" in blob:
                return True, "pymobiledevice3 auto-mount ok"
            pmd_msg = (res.stderr or res.stdout or "").strip()[-200:]
        except Exception as exc:  # noqa: BLE001 — best-effort, fall through to go-ios
            pmd_msg = str(exc)
        ok, gmsg = self.goios.mount_developer_image(udid, self.config.get("IOS_DDI_CACHE") or None)
        return ok, (gmsg if ok else f"pymobiledevice3: {pmd_msg}; go-ios: {gmsg}")

    def _bring_up_wda(self, udid: str, controller) -> None:
        """Auto-start WebDriverAgent if it isn't answering.

        Preferred path (no admin): go-ios userspace tunnel + ``ios runwda``.
        Fallback: the pymobiledevice3 xcuitest launcher (needs an admin
        tunneld), only when explicitly enabled via IOS_AUTO_LAUNCH_WDA.
        """
        launch_timeout = float(self.config.get("IOS_WDA_LAUNCH_TIMEOUT", 40))

        if self.use_goios and self.goios.is_available():
            if self._goios_tunnel_enabled:
                ok, msg = self.tunnel.ensure_running(timeout=launch_timeout)
                if not ok:
                    _log.warning("go-ios tunnel not ready: %s", msg)
            # iOS 17+ xctest/WDA needs the Developer Disk Image mounted, or
            # runwda fails with "cannot initiate an IDE session: ... broken pipe".
            # Auto-mount it (no-admin, via the tunnel; cached + fast no-op when
            # already mounted) so connect doesn't depend on a manual mount step.
            # Cache dir defaults to resources/devimages (override: IOS_DDI_CACHE).
            mounted, mmsg = self._mount_ddi(udid)
            # Best-effort only: go-ios's `image auto` matches the DDI per device/
            # version itself. If it didn't mount (e.g. a DDI is already mounted by
            # Xcode, or go-ios mis-resolved a too-new iOS), DON'T block — still try
            # runwda; it works whenever a valid DDI is present by any means.
            if mounted:
                self._ddi_error.pop(udid, None)
            else:
                self._ddi_error[udid] = str(mmsg)
                _log.info("DDI auto-mount not confirmed for %s (continuing to runwda anyway): %s",
                          udid, str(mmsg)[-160:])
            proc = self._wda_procs.get(udid)
            if proc is None or proc.poll() is not None:
                bundle = self.config.get(
                    "IOS_WDA_BUNDLE_ID", "com.facebook.WebDriverAgentRunner.xctrunner"
                )
                _log.info("launching WDA via go-ios runwda for %s (%s)", udid, bundle)
                self._wda_procs[udid] = self.goios.run_wda(
                    udid,
                    bundle_id=bundle,
                    test_runner_bundle_id=self.config.get("IOS_WDA_TEST_RUNNER_BUNDLE_ID") or None,
                    xctest_config=self.config.get("IOS_WDA_XCTEST_CONFIG", "WebDriverAgentRunner.xctest"),
                    env={
                        "USE_PORT": str(self.wda_remote_port),
                        "MJPEG_SERVER_PORT": str(int(self.config.get("IOS_MJPEG_REMOTE_PORT", 9100))),
                    },
                )
            self._poll_wda(udid, controller, launch_timeout, lambda: self._wda_procs.get(udid))
            return

        if self.auto_launch_wda:
            _log.info("WDA not answering; auto-launching via pymobiledevice3 for %s", udid)
            self.wda_launcher.launch(udid)
            self._poll_wda(udid, controller, launch_timeout, lambda: self.wda_launcher.process_for(udid))

    def _wda_launch_output(self, udid: str) -> str:
        """Tail of the runwda launcher's captured stdout/stderr (the reason it
        died, if it did). Empty when there's no log file."""
        proc = self._wda_procs.get(udid)
        path = getattr(proc, "_goios_log", None)
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    @staticmethod
    def _poll_wda(udid, controller, timeout, proc_getter) -> None:
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            proc = proc_getter()
            if proc is not None and proc.poll() is not None:
                # Launcher exited early (signing / trust / device-locked / tunnel).
                out = ""
                path = getattr(proc, "_goios_log", None)
                if path:
                    with suppress(OSError):
                        with open(path, "r", encoding="utf-8", errors="replace") as fh:
                            out = fh.read().strip()[-800:]
                _log.warning("WDA launcher for %s exited early (code %s)%s",
                             udid, proc.poll(), f":\n{out}" if out else "")
                break
            if controller.health_check():
                _log.info("WDA came up for %s", udid)
                return
            time.sleep(1.0)

    def disconnect(self, udid: str) -> None:
        # Close any WebRTC peer connections for this device FIRST. That cascades
        # into track.stop() → provider.stop() which sets _stopping=True on the
        # MJPEG reader, so the in-flight chunked-stream read that's about to die
        # when we tear down port forwards below is recognized as an intentional
        # shutdown (DEBUG log) instead of a WinError 10054 traceback (WARN).
        with suppress(Exception):
            self.webrtc.stop_device(udid)
        self.stop_stream(udid)
        with self._lock:
            controller = state.controllers.pop(udid, None)
        if controller:
            controller.close()
        if self.auto_launch_wda:
            self.wda_launcher.stop(udid)
        proc = self._wda_procs.pop(udid, None)
        if proc is not None and proc.poll() is None:
            with suppress(Exception):
                proc.terminate()
        if proc is not None:
            log_path = getattr(proc, "_goios_log", None)
            if log_path:
                with suppress(OSError):
                    os.remove(log_path)
        self.port_forward.stop_forward(udid)
        self.port_forward.stop_forward(f"{udid}#mjpeg")
        device = state.get_device(udid)
        if device:
            device.connected = False
            device.wda_running = False
            device.streaming = False
            device.local_wda_port = None
            device.screen_provider = None
        state.forget(udid)  # clear idle/viewer bookkeeping for the released device

    def check_wda(self, udid: str) -> bool:
        device = state.get_device(udid)
        if not device or not device.local_wda_port:
            return False
        controller = self._controller(udid, device.local_wda_port)
        alive = controller.health_check()
        device.wda_running = alive
        return alive

    # ── accessibility quick-toggles (go-ios; works over usbmux, no tunnel) ──
    _ACCESSIBILITY = ("assistivetouch", "voiceover", "zoom")

    def accessibility(self, udid: str, feature: str, action: str = "toggle") -> dict:
        """Toggle/query an iOS accessibility software button via go-ios
        (`ios assistivetouch|voiceover|zoom enable|disable|toggle|get`)."""
        import json as _json

        feature = (feature or "").strip().lower()
        action = (action or "toggle").strip().lower()
        if feature not in self._ACCESSIBILITY:
            raise AppError(ErrorCode.BAD_REQUEST,
                           f"unknown feature '{feature}' ({'/'.join(self._ACCESSIBILITY)})")
        if action not in ("toggle", "enable", "disable", "get"):
            raise AppError(ErrorCode.BAD_REQUEST,
                           f"unknown action '{action}' (toggle/enable/disable/get)")
        if not (self.use_goios and self.goios.is_available()):
            raise AppError(ErrorCode.INTERNAL_ERROR, "go-ios not available for accessibility")
        rc, out, err = self.goios.run([feature, action], udid=udid, timeout=20)
        if rc != 0:
            raise AppError(ErrorCode.INTERNAL_ERROR,
                           f"go-ios {feature} {action} failed", {"stderr": (err or "")[-200:]})
        enabled = None
        for line in reversed((out or "").strip().splitlines()):
            try:
                data = _json.loads(line)
                enabled = next((v for k, v in data.items() if "Enabled" in k), None)
                break
            except ValueError:
                continue
        return {"feature": feature, "action": action, "enabled": enabled}

    def set_mjpeg_scaling(self, udid: str, scaling: int) -> dict:
        """Live-adjust WDA's MJPEG downscale % (mjpegScalingFactor). Used by the
        multi-device grid to drop to ~70% (CPU relief — the re-encode cost scales
        with resolution × device count) and restore 100% when back to single view.
        Best-effort: needs WDA up; never raises."""
        scaling = max(10, min(100, int(scaling)))
        device = state.get_device(udid)
        applied = False
        if device and device.local_wda_port:
            with suppress(Exception):
                self._controller(udid, device.local_wda_port).update_settings(
                    {"mjpegScalingFactor": scaling}
                )
                applied = True
        return {"udid": udid, "scaling": scaling, "applied": applied}

    # ── controllers ──────────────────────────────────────────────────
    def controller(self, udid: str) -> WDAController:
        device = state.get_device(udid)
        if not device or not device.local_wda_port:
            raise AppError(
                ErrorCode.WDA_NOT_RUNNING,
                f"Device {udid} is not connected (no WDA port). Call connect first.",
            )
        return self._controller(udid, device.local_wda_port)

    def _controller(self, udid: str, local_port: int) -> WDAController:
        with self._lock:
            ctrl = state.controllers.get(udid)
            if ctrl is None or getattr(ctrl, "base_url", "").endswith(str(local_port)) is False:
                ctrl = WDAController(
                    base_url=f"http://127.0.0.1:{local_port}", timeout=self.wda_timeout
                )
                state.controllers[udid] = ctrl
            return ctrl

    # ── screen providers ─────────────────────────────────────────────
    def make_screen_provider(
        self, udid: str, provider_name: Optional[str] = None, fps: Optional[int] = None
    ) -> BaseScreenProvider:
        device = state.get_device(udid)
        if not device or not device.local_wda_port:
            raise AppError(ErrorCode.WDA_NOT_RUNNING, "Connect the device first")
        provider_name = (provider_name or self.config.get("IOS_SCREEN_PROVIDER", "mjpeg")).lower()
        controller = self._controller(udid, device.local_wda_port)
        shot_fps = int(fps or self.config.get("IOS_SCREENSHOT_FPS", 8))

        if provider_name == "mjpeg":
            # WDA's MJPEG server listens on device port 9100; reuse the same
            # forwarded host but the MJPEG port. For phase 1 we forward 9100
            # alongside 8100 lazily via the same local port base + 1000.
            mjpeg_port = self._ensure_mjpeg_forward(udid)
            return WdaMjpegProvider(mjpeg_url=f"http://127.0.0.1:{mjpeg_port}")

        # default / fallback
        return WdaScreenshotProvider(
            controller,
            fps=shot_fps,
        )

    def fallback_provider(self, udid: str, fps: Optional[int] = None) -> BaseScreenProvider:
        device = state.get_device(udid)
        if not device or not device.local_wda_port:
            # No WDA forward → there's nothing to screenshot. Surface a clean
            # WDA_NOT_RUNNING instead of building an http://127.0.0.1:None URL.
            raise AppError(ErrorCode.WDA_NOT_RUNNING, "Connect the device first")
        controller = self._controller(udid, device.local_wda_port)
        return WdaScreenshotProvider(
            controller, fps=int(fps or self.config.get("IOS_SCREENSHOT_FPS", 8))
        )

    def _ensure_mjpeg_forward(self, udid: str) -> int:
        """Forward the WDA MJPEG port (9100) for this device; return local port.

        Tracked under a distinct ``"<udid>#mjpeg"`` key in the same
        port-forward registry so it has an independent lifecycle from the
        WDA-control (8100) forward.
        """
        key = f"{udid}#mjpeg"
        if self.port_forward.health_check(key):
            return self.port_forward.local_port(key)  # type: ignore[return-value]
        base = int(self.config.get("IOS_LOCAL_PORT_START", 18100)) + 1000
        from utils.port_utils import find_free_port

        local = find_free_port(base)
        remote = int(self.config.get("IOS_MJPEG_REMOTE_PORT", 9100))
        handle = self.port_forward.start_forward(
            key, local_port=local, remote_port=remote, device_udid=udid
        )
        return handle.local_port

    # ── stream lifecycle (delegated to StreamService via registry) ───
    @staticmethod
    def stop_stream(udid: str) -> None:
        session = state.streams.get(udid)
        if session and hasattr(session, "stop"):
            session.stop()

    def shutdown(self) -> None:
        with suppress(Exception):
            self.webrtc.shutdown()
        for udid in list(state.streams.keys()):
            self.stop_stream(udid)
        for proc in self._wda_procs.values():
            if proc is not None and proc.poll() is None:
                with suppress(Exception):
                    proc.terminate()
        self._wda_procs.clear()
        self.wda_launcher.stop_all()
        if self._goios_tunnel_enabled:
            with suppress(Exception):
                self.tunnel.stop()
        self.port_forward.stop_all()
