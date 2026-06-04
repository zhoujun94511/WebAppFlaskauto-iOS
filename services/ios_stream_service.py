"""Stream orchestration: choose provider, start/stop, fallback + self-heal.

If a provider dies mid-stream (e.g. WDA's MJPEG socket drops during heavy
input), the session thread reports it via ``on_unhealthy``. We then auto-recover
by restarting the stream on the **screenshot** provider (no long-lived socket,
so it survives input bursts), bounded by ``IOS_STREAM_MAX_RECOVERY`` so a truly
dead device can't spin forever. A user-initiated stop cancels recovery; a
user-initiated start resets the budget.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from services import get_adapter
from services.runtime_state import state
from services.stream_bridge import StreamSession
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class IOSStreamService:
    def __init__(self, config: dict):
        self.config = config
        self.max_fps = int(config.get("IOS_STREAM_MAX_FPS", 15))
        self.max_recovery = int(config.get("IOS_STREAM_MAX_RECOVERY", 3))
        self._lock = threading.Lock()
        self._recovery_count: dict[str, int] = {}
        self._stream_params: dict[str, dict] = {}
        self._no_recover: set[str] = set()  # udids the user explicitly stopped

    def choose_provider(self, _udid: str, provider: Optional[str]) -> str:
        return (provider or self.config.get("IOS_SCREEN_PROVIDER", "mjpeg")).lower()

    @staticmethod
    def _clamp_fps(fps: Optional[int]) -> Optional[int]:
        if fps is None:
            return None
        try:
            return max(1, min(30, int(fps)))
        except (TypeError, ValueError):
            return None

    def start_stream(
        self,
        udid: str,
        provider: Optional[str] = None,
        fps: Optional[int] = None,
        _recovery: bool = False,
    ) -> dict:
        adapter = get_adapter()
        # WDA is a flaky XCTest process — it can die between connect and now
        # (device switch, idle, crash) while the device still reports
        # connected=True. Probe it and re-establish if it's not answering, so
        # switching back to a device / restarting a stream "just works" instead
        # of failing with WDA-not-reachable.
        if not adapter.check_wda(udid):
            _log.info("stream %s: WDA not answering — re-establishing before stream", udid)
            try:
                adapter.connect(udid)
            except AppError as exc:
                _log.warning("stream %s: re-establish failed (%s)", udid, exc.code)
        self._stop_session(udid)  # clean restart (does NOT cancel recovery)
        provider_name = self.choose_provider(udid, provider)
        fps = self._clamp_fps(fps)
        session_fps = fps or self.max_fps
        with self._lock:
            self._stream_params[udid] = {"fps": fps}
            if not _recovery:
                # A user/initial start clears the recovery budget + stop flag.
                self._recovery_count.pop(udid, None)
                self._no_recover.discard(udid)
        try:
            sp = adapter.make_screen_provider(udid, provider_name, fps=fps)
            session = StreamSession(
                udid, sp, max_fps=session_fps, on_unhealthy=self._on_unhealthy
            )
            session.start()
        except AppError as exc:
            # Only fall back to the screenshot provider for a *real* MJPEG
            # failure (WDA is up but its MJPEG server/forward isn't usable).
            # If the device isn't connected (no WDA / no forward port), the
            # screenshot path can't work either — re-raise so the client gets
            # a clean WDA_NOT_RUNNING instead of a httpx InvalidURL crash.
            non_recoverable = {ErrorCode.WDA_NOT_RUNNING, ErrorCode.DEVICE_DISCONNECTED}
            if provider_name == "mjpeg" and exc.code not in non_recoverable:
                _log.warning("MJPEG provider failed (%s); falling back to screenshot", exc.code)
                sp = adapter.fallback_provider(udid, fps=fps)
                session = StreamSession(
                    udid, sp, max_fps=session_fps, on_unhealthy=self._on_unhealthy,
                )
                session.start()
            else:
                raise
        state.streams[udid] = session
        dev = state.get_device(udid)
        if dev:
            dev.streaming = True
            dev.screen_provider = session.provider_name
        return session.status()

    def _on_unhealthy(self, udid: str) -> None:
        # Called from the dying stream thread — must NOT restart synchronously
        # (start_stream joins that very thread → self-deadlock). Hand off to a
        # fresh thread for bounded auto-recovery.
        state.streams.pop(udid, None)
        dev = state.get_device(udid)
        if dev:
            dev.streaming = False
        threading.Thread(
            target=self._recover, args=(udid,), name=f"recover-{udid}", daemon=True
        ).start()

    def _recover(self, udid: str) -> None:
        with self._lock:
            if udid in self._no_recover:
                return  # user stopped it; don't resurrect
            n = self._recovery_count.get(udid, 0)
            if n >= self.max_recovery:
                _log.warning("stream %s: auto-recovery exhausted after %d tries", udid, n)
                return
            self._recovery_count[udid] = n + 1
            fps = (self._stream_params.get(udid) or {}).get("fps")
        time.sleep(1.0)  # let the failed provider/socket fully tear down
        dev = state.get_device(udid)
        if not dev or not dev.connected or not dev.local_wda_port:
            _log.info("stream %s: device gone, skipping recovery", udid)
            return
        if udid in self._no_recover:
            return
        try:
            _log.info("stream %s: auto-recovering via screenshot (attempt %d/%d)",
                      udid, n + 1, self.max_recovery)
            self.start_stream(udid, provider="screenshot", fps=fps, _recovery=True)
        except AppError as exc:
            _log.warning("stream %s: recovery attempt failed (%s)", udid, exc.code)

    @staticmethod
    def _stop_session(udid: str) -> None:
        """Stop the running session (internal; does not touch recovery state)."""
        session = state.streams.pop(udid, None)
        if session:
            session.stop()
        dev = state.get_device(udid)
        if dev:
            dev.streaming = False

    def stop_stream(self, udid: str) -> None:
        """User-initiated stop: cancel any pending/future auto-recovery too."""
        with self._lock:
            self._no_recover.add(udid)
            self._recovery_count.pop(udid, None)
        self._stop_session(udid)

    @staticmethod
    def get_status(udid: str) -> dict:
        session = state.streams.get(udid)
        if not session:
            return {"udid": udid, "running": False, "provider": None, "frames": 0}
        return session.status()

    def fallback_provider(self, udid: str) -> dict:
        """Force-switch a running stream to the screenshot fallback."""
        return self.start_stream(udid, provider="screenshot")
