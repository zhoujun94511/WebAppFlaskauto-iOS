"""Device-level business logic. API/Socket handlers call into here."""

from __future__ import annotations

from typing import List

from services import get_adapter
from services.event_bus import emit
from utils.app_errors import AppError, ErrorCode


class IOSDeviceService:
    @staticmethod
    def backend_available() -> bool:
        return get_adapter().is_backend_available()

    @staticmethod
    def list_devices(rescan: bool = True) -> List[dict]:
        adapter = get_adapter()
        if rescan:
            devices = adapter.discover()
        else:
            from services.runtime_state import state

            devices = state.list_devices()
        return [d.to_dict() for d in devices]

    @staticmethod
    def get_device(udid: str, refresh: bool = True) -> dict:
        adapter = get_adapter()
        if refresh:
            try:
                adapter.refresh_device(udid)
            except AppError:
                pass
        from services.runtime_state import state

        dev = state.get_device(udid)
        if not dev:
            raise AppError(ErrorCode.NO_IOS_DEVICE, f"Device {udid} not found")
        return dev.to_dict()

    @staticmethod
    def connect_device(udid: str) -> dict:
        device = get_adapter().connect(udid).to_dict()
        emit("device:connected", {"udid": udid, "device": device})
        emit("wda:status", {"udid": udid, "wda_running": device.get("wda_running")})
        return device

    @staticmethod
    def disconnect_device(udid: str) -> None:
        get_adapter().disconnect(udid)
        emit("device:disconnected", {"udid": udid})

    @staticmethod
    def check_wda(udid: str) -> bool:
        running = get_adapter().check_wda(udid)
        emit("wda:status", {"udid": udid, "wda_running": running})
        return running
