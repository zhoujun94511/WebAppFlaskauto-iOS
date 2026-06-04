"""Process-wide in-memory state (no DB in phase 1).

Holds the live :class:`IOSDevice` registry, per-device WDA controllers,
port-forward handles and active stream sessions. A single lock guards
mutations. When the unified platform later adds persistence, this becomes
the cache layer in front of it.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Set

# Imported lazily-typed to avoid a hard import cycle at module load.
from ios.device_models import IOSDevice


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.devices: Dict[str, IOSDevice] = {}          # udid -> IOSDevice
        self.controllers: Dict[str, object] = {}          # udid -> WDAController
        self.forwards: Dict[str, object] = {}             # udid -> forward handle
        self.streams: Dict[str, object] = {}              # udid -> StreamSession
        self.last_activity: Dict[str, float] = {}         # udid -> monotonic ts
        self.viewers: Dict[str, Set[str]] = {}            # udid -> {socket sid}

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # ── activity / presence (for idle auto-release) ──────────────────
    def touch(self, udid: str) -> None:
        """Record activity (connect / control input / stream start) on a device."""
        with self._lock:
            self.last_activity[udid] = time.monotonic()

    def idle_seconds(self, udid: str) -> float:
        with self._lock:
            ts = self.last_activity.get(udid)
            return float("inf") if ts is None else (time.monotonic() - ts)

    def add_viewer(self, udid: str, sid: str) -> None:
        with self._lock:
            self.viewers.setdefault(udid, set()).add(sid)

    def remove_viewer(self, udid: str, sid: str) -> None:
        with self._lock:
            s = self.viewers.get(udid)
            if s:
                s.discard(sid)

    def viewer_count(self, udid: str) -> int:
        with self._lock:
            return len(self.viewers.get(udid, ()))

    def forget(self, udid: str) -> None:
        """Clear presence/activity bookkeeping for a released device."""
        with self._lock:
            self.viewers.pop(udid, None)
            self.last_activity.pop(udid, None)

    def drop_sid(self, sid: str) -> List[str]:
        """Remove a disconnected client's sid from all viewer sets; return the
        udids that now have zero viewers (their stream can be stopped)."""
        emptied: List[str] = []
        with self._lock:
            for udid, s in self.viewers.items():
                if sid in s:
                    s.discard(sid)
                    if not s:
                        emptied.append(udid)
        return emptied

    # ── devices ──────────────────────────────────────────────────────
    def upsert_device(self, device: IOSDevice) -> IOSDevice:
        with self._lock:
            existing = self.devices.get(device.udid)
            if existing:
                existing.merge(device)
                return existing
            self.devices[device.udid] = device
            return device

    def get_device(self, udid: str) -> Optional[IOSDevice]:
        with self._lock:
            return self.devices.get(udid)

    def list_devices(self) -> list[IOSDevice]:
        with self._lock:
            return list(self.devices.values())

    def remove_missing(self, present_udids: set[str]) -> list[str]:
        """Drop devices no longer physically present (unplugged) and return their
        udids so the caller can tear down forwards/WDA.

        Previously this only flipped ``connected=False`` and KEPT the entry, so an
        unplugged device lingered in ``list_devices()`` forever — showing a ghost
        "连接" card and never registering as a hotplug disconnect. A device absent
        from a fresh usbmux scan is gone; remove it (and its viewer/activity
        bookkeeping) entirely."""
        gone = []
        with self._lock:
            for udid in list(self.devices.keys()):
                if udid not in present_udids:
                    gone.append(udid)
                    self.devices.pop(udid, None)
                    self.viewers.pop(udid, None)
                    self.last_activity.pop(udid, None)
        return gone


# Module-level singleton.
state = RuntimeState()
