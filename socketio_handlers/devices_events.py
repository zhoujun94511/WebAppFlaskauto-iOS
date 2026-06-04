"""Device Socket.IO events."""

from __future__ import annotations

from services.ios_device_service import IOSDeviceService
from utils.app_errors import AppError

_svc = IOSDeviceService()


def register(socketio) -> None:
    @socketio.on("devices:list")
    def _list(_data=None):
        try:
            devices = _svc.list_devices(rescan=False)
            socketio.emit("devices:changed", {"devices": devices})
        except AppError as exc:
            socketio.emit("stream:error", exc.to_dict())

    @socketio.on("devices:refresh")
    def _refresh(_data=None):
        try:
            devices = _svc.list_devices(rescan=True)
            socketio.emit("devices:changed", {"devices": devices})
        except AppError as exc:
            socketio.emit("stream:error", exc.to_dict())
