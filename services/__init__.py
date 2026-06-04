"""Service layer + the platform-adapter accessor.

``init_platform(config)`` is called once at app startup; services then call
``get_adapter()``. Today that's always the IOSAdapter; the unified platform
will pick android/ios by device. Import of the adapter is lazy to dodge an
import cycle (adapter → runtime_state → services package).
"""

from __future__ import annotations

_adapter = None


def init_platform(config: dict):
    global _adapter
    from ios.ios_adapter import IOSAdapter  # lazy

    _adapter = IOSAdapter(config)
    return _adapter


def get_adapter():
    if _adapter is None:
        raise RuntimeError("Platform adapter not initialized — call init_platform first")
    return _adapter
