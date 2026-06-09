"""Device model. Field names are chosen so a future Android model can share
the same shape (``platform`` discriminator + common fields)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class IOSDevice:
    platform: str = "ios"
    udid: str = ""
    name: str = ""
    product_type: str = ""          # e.g. iPhone14,5
    ios_version: str = ""
    connected: bool = False
    developer_mode: str = "unknown"  # "on" | "off" | "unknown"
    trusted: bool = False
    wda_running: bool = False
    local_wda_port: Optional[int] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    orientation: str = "PORTRAIT"
    streaming: bool = False
    screen_provider: Optional[str] = None
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        # Stamp `marketing` derived from product_type so every list-view
        # consumer (DeviceStage / DeviceMatrix / DeviceStrip) gets the friendly
        # name (e.g. "iPhone 15 Pro Max") without each one re-fetching the
        # /info detail endpoint. Late import keeps the dataclass import-light.
        from ios.device_marketing import marketing_name
        d = asdict(self)
        d["marketing"] = marketing_name(self.product_type) if self.product_type else ""
        return d

    def merge(self, other: "IOSDevice") -> None:
        """Update mutable fields from a freshly-scanned device, preserving
        runtime-only fields (ports, wda_running, streaming) unless the new
        scan has better info."""
        self.name = other.name or self.name
        self.product_type = other.product_type or self.product_type
        self.ios_version = other.ios_version or self.ios_version
        # NOTE: do NOT copy `other.connected` / wda_running / local_wda_port /
        # streaming — those are app-level RUNTIME state owned by connect()/
        # disconnect(). A periodic rescan (which always reports connected=False)
        # must not clobber a device we've actually connected. Device removal on
        # unplug is handled separately by state.remove_missing().
        if other.developer_mode != "unknown":
            self.developer_mode = other.developer_mode
        self.trusted = other.trusted or self.trusted
        if other.screen_width:
            self.screen_width = other.screen_width
        if other.screen_height:
            self.screen_height = other.screen_height
        self.last_seen = other.last_seen

    @staticmethod
    def from_pymobiledevice3_output(info: dict) -> "IOSDevice":
        """Build from a ``pymobiledevice3 usbmux list`` / lockdown info dict.

        Field names vary across pymobiledevice3 versions, so we probe several
        common aliases and degrade gracefully (missing → empty/unknown).
        """
        def pick(*keys, default=""):
            for k in keys:
                v = info.get(k)
                if v not in (None, ""):
                    return v
            return default

        udid = pick("Identifier", "UniqueDeviceID", "udid", "SerialNumber")
        name = pick("DeviceName", "Name", "name")
        product_type = pick("ProductType", "product_type")
        ios_version = pick("ProductVersion", "ios_version", "os_version")
        # Being in ``usbmux list`` means the device is plugged in & paired
        # (trusted/reachable) — but NOT app-level connected. ``connected`` must
        # stay False here and only flip True after adapter.connect() brings up
        # the WDA forward; otherwise the UI thinks it's already connected and
        # skips connect(), so WebDriverAgent never launches.
        reachable = bool(pick("ConnectionType", "connected", default="")) or bool(udid)
        return IOSDevice(
            udid=str(udid),
            name=str(name) or str(udid),
            product_type=str(product_type),
            ios_version=str(ios_version),
            connected=False,
            trusted=bool(info.get("trusted", reachable)),
        )
