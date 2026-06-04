"""Idle device auto-release for the shared device pool.

Devices are a shared resource: if an operator connects and walks away (closes
the tab, stops the stream), the per-device WebDriverAgent + USB forwards would
otherwise stay up forever, holding the device. This background reaper releases
(Disconnect — tears down WDA + forwards) any device that is connected but has
**no viewers and no control activity** for longer than the configured timeout.

The shared go-ios tunnel agent is left running (it serves all devices and is
only reclaimed when the backend process exits).
"""

from __future__ import annotations

import threading
from contextlib import suppress

from services import event_bus as events
from services.runtime_state import state
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class IdleReaper:
    def __init__(self, timeout_s: int, interval_s: int | None = None):
        self.timeout = max(0, int(timeout_s))
        # Check often enough to honour the timeout without busy-spinning; cap at
        # 30s so a long timeout doesn't make releases feel sluggish.
        self.interval = int(interval_s) if interval_s else max(2, min(30, (self.timeout // 2) or 30))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.timeout <= 0:
            _log.info("idle reaper disabled (IOS_DEVICE_IDLE_TIMEOUT<=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="idle-reaper", daemon=True)
        self._thread.start()
        _log.info("idle reaper started (release after %ds idle, checking every %ds)",
                  self.timeout, self.interval)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from services import get_adapter
        while not self._stop.wait(self.interval):
            with suppress(Exception):
                self._tick(get_adapter())

    def _tick(self, adapter) -> None:
        for dev in list(state.list_devices()):
            if not getattr(dev, "connected", False):
                continue
            udid = dev.udid
            if state.viewer_count(udid) > 0:
                continue  # someone is actively watching → keep it
            idle = state.idle_seconds(udid)
            if idle <= self.timeout:
                continue
            _log.info("idle auto-release: %s idle %.0fs (>%ds), no viewers — disconnecting",
                      udid, idle, self.timeout)
            try:
                adapter.disconnect(udid)
            except Exception as exc:  # noqa: BLE001
                _log.warning("idle release of %s failed: %s", udid, exc)
                continue
            # Tell connected clients the device is now free.
            with suppress(Exception):
                from services.ios_device_service import IOSDeviceService
                events.emit("devices:changed",
                            {"devices": IOSDeviceService().list_devices(rescan=False)})
