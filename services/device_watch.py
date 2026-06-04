"""USB hotplug watcher — broadcast device-list changes over Socket.IO.

A small polling thread re-scans the connected device set (via usbmux) every
``IOS_DEVICE_WATCH_INTERVAL`` seconds and compares it to the previous snapshot.
On any plug/unplug it pushes a ``devices:changed`` event so every browser tab
refreshes its grid/strip without the user hitting "刷新".

Mirrors the Android sibling's ``services/device_watch.py``. iOS has no cheap
event-stream for attach/detach the way ``adb track-devices`` does, so we poll —
``list_devices(rescan=True)`` is just an usbmux enumeration (tens of ms).

On disconnect, we best-effort tear down our side (WDA + port forwards) for the
vanished device so a later re-plug starts clean, and we don't leak forwards.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Set

from services import event_bus as events
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class DeviceWatcher:
    def __init__(self, interval_s: float = 3.0):
        self.interval = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: Set[str] = set()

    def start(self) -> None:
        if self.interval <= 0:
            _log.info("device watcher disabled (IOS_DEVICE_WATCH_INTERVAL<=0)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="device-watch", daemon=True)
        self._thread.start()
        _log.info("device watcher started (hotplug poll every %.1fs)", self.interval)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from services.ios_device_service import IOSDeviceService

        svc = IOSDeviceService()
        # Prime the snapshot with whatever is connected at startup so the first
        # real plug/unplug is what triggers a broadcast (not the initial scan).
        with suppress(Exception):
            self._last = {d["udid"] for d in svc.list_devices(rescan=True)}

        while not self._stop.wait(self.interval):
            with suppress(Exception):
                self._tick(svc)

    def _tick(self, svc) -> None:
        devices = svc.list_devices(rescan=True)
        current = {d["udid"] for d in devices}
        if current == self._last:
            return
        added = sorted(current - self._last)
        removed = sorted(self._last - current)
        if added:
            _log.info("device connected: %s", added)
        if removed:
            _log.info("device disconnected: %s", removed)
        for udid in removed:
            self._on_removed(udid)
        self._last = current
        # Broadcast the fresh list (same shape as devices:list/refresh) so every
        # tab updates its grid/strip immediately.
        events.emit("devices:changed", {"devices": devices})

    @staticmethod
    def _on_removed(udid: str) -> None:
        """Best-effort teardown for a device that vanished from usbmux: stop its
        WebRTC peers + streams + WDA/forwards so a re-plug starts clean."""
        from services import get_adapter

        with suppress(Exception):
            get_adapter().disconnect(udid)
