"""Optionally launch the WDA XCUITest runner via pymobiledevice3.

iOS 17+ needs a RemoteServiceDiscovery tunnel running (``pymobiledevice3 remote
tunneld``, admin) before XCUITest can start — we can't create that tunnel from
here (privileges), but we can launch the runner and surface a clear
IOS17_TUNNEL_FAILED hint when the tunnel is missing.

Disabled by default (``IOS_AUTO_LAUNCH_WDA=0``); when enabled, the adapter calls
``ensure_running`` during connect if WDA isn't answering.
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import suppress
from typing import Dict, Optional

from services.command_runner import pymobiledevice3_cmd
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class WDALauncher:
    def __init__(
        self,
        bundle_id: str,
        launch_timeout: float = 40.0,
        env: Optional[Dict[str, str]] = None,
    ):
        self.bundle_id = bundle_id
        self.launch_timeout = launch_timeout
        # Env passed to the WDA runner. Critically includes USE_PORT (control,
        # 8100) and MJPEG_SERVER_PORT (9100) — without MJPEG_SERVER_PORT this
        # WDA build does NOT open its MJPEG broadcaster, so the screen stream
        # falls back to screenshots. See README §(c).
        self.env = dict(env or {})
        self._procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def is_running(self, udid: str) -> bool:
        with self._lock:
            p = self._procs.get(udid)
            return bool(p and p.poll() is None)

    def launch(self, udid: str) -> subprocess.Popen:
        """Start (or reuse) a background xcuitest runner process for ``udid``."""
        with self._lock:
            existing = self._procs.get(udid)
            if existing and existing.poll() is None:
                return existing
            args = pymobiledevice3_cmd() + [
                "developer", "dvt", "xcuitest", self.bundle_id, "--udid", udid,
            ]
            for k, v in self.env.items():
                args += ["--env", f"{k}={v}"]
            _log.info(
                "launching WDA xcuitest for %s: %s env=%s",
                udid, self.bundle_id, self.env,
            )
            proc = subprocess.Popen(
                [str(a) for a in args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._procs[udid] = proc
            return proc

    def stop(self, udid: str) -> None:
        with self._lock:
            proc = self._procs.pop(udid, None)
        if not proc:
            return
        with suppress(OSError, subprocess.SubprocessError):
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                proc.kill()

    def read_early_output(self, udid: str, max_bytes: int = 800) -> str:
        """Non-blocking-ish peek at the runner's output for error hints."""
        proc = self._procs.get(udid)
        if not proc or proc.poll() is None or not proc.stdout:
            return ""
        try:
            return (proc.stdout.read() or b"").decode("utf-8", "replace")[:max_bytes]
        except (OSError, UnicodeDecodeError, ValueError):
            return ""

    def process_for(self, udid: str) -> Optional[subprocess.Popen]:
        """Return the tracked runner process for ``udid`` if it exists."""
        return self._procs.get(udid)

    def stop_all(self) -> None:
        for udid in list(self._procs.keys()):
            self.stop(udid)
