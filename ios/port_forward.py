"""Forward a local TCP port to the device's WDA ports via pymobiledevice3.

Each device gets its own local port (allocated from IOS_LOCAL_PORT_START up).
The forward runs as a long-lived child process; we track it, health-check the
listening socket, and clean it up on stop. iOS 17+ requires a lockdown tunnel
first — we surface a clear IOS17_TUNNEL_FAILED hint when that's the cause.

IMPORTANT: the child's stdout/stderr must go to DEVNULL — NOT a PIPE. A
high-volume relay (the MJPEG stream on 9100) makes pymobiledevice3 emit
carriage-return progress output; an unread (or even drained) PIPE buffer fills,
the forwarder blocks on its write, and forwarded connections start getting
reset (WinError 10053). DEVNULL is the proven-stable config.
"""

from __future__ import annotations

import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Dict, Optional

from services.command_runner import pymobiledevice3_cmd
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger
from utils.port_utils import find_free_port, is_port_open

_log = get_logger(__name__)


@dataclass
class ForwardHandle:
    udid: str
    local_port: int
    remote_port: int
    process: subprocess.Popen


class IOSPortForward:
    def __init__(self, local_port_start: int = 18100):
        self.local_port_start = local_port_start
        self._handles: Dict[str, ForwardHandle] = {}
        self._lock = threading.Lock()

    def start_forward(
        self,
        key: str,
        local_port: Optional[int] = None,
        remote_port: int = 8100,
        device_udid: Optional[str] = None,
    ) -> ForwardHandle:
        # ``key`` is the registry key (maybe suffixed, e.g. "<udid>#mjpeg" for
        # a second forward to the same device). ``device_udid`` is the REAL
        # device id passed to ``--udid``; it defaults to ``key`` for the common
        # case where the key *is* the udid. Mixing these up makes the local
        # port listen but every upstream connect fail (invalid --udid).
        udid = device_udid or key
        with self._lock:
            existing = self._handles.get(key)
            if existing and existing.process.poll() is None:
                return existing

            if local_port is None:
                try:
                    local_port = find_free_port(self.local_port_start)
                except OSError as exc:
                    raise AppError(
                        ErrorCode.LOCAL_PORT_IN_USE,
                        "No free local port for WDA forward",
                        {"start": self.local_port_start},
                    ) from exc

            args = pymobiledevice3_cmd() + [
                "usbmux", "forward", str(local_port), str(remote_port), "--udid", udid,
            ]
            _log.info("starting forward %s: %d -> %d (udid=%s)", key, local_port, remote_port, udid)
            # CRITICAL: both streams to DEVNULL. pymobiledevice3 writes
            # carriage-return progress output; a stderr PIPE (even drained)
            # makes the high-volume MJPEG relay stall and reset forwarded
            # connections (WinError 10053). DEVNULL is the proven-stable config.
            try:
                proc = subprocess.Popen(
                    [str(a) for a in args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                raise AppError(
                    ErrorCode.PYMOBILEDEVICE3_NOT_INSTALLED,
                    "pymobiledevice3 not available for port forward",
                    {"error": str(exc)},
                ) from exc

            handle = ForwardHandle(key, local_port, remote_port, proc)
            self._handles[key] = handle

        if not self._wait_listening(local_port, proc, timeout=8.0):
            early_exit = proc.poll() is not None
            self.stop_forward(key)
            # No stderr to inspect (DEVNULL); an early exit on iOS 17+ is most
            # often the missing RemoteServiceDiscovery tunnel.
            if early_exit:
                raise AppError(
                    ErrorCode.IOS17_TUNNEL_FAILED,
                    "Port forward process exited early — on iOS 17+ start a tunnel "
                    "first ('pymobiledevice3 remote tunneld', admin).",
                    {"local_port": local_port, "remote_port": remote_port},
                )
            raise AppError(
                ErrorCode.PORT_FORWARD_FAILED,
                "Port forward did not start listening",
                {"local_port": local_port, "remote_port": remote_port},
            )
        return handle

    @staticmethod
    def _wait_listening(port: int, proc: subprocess.Popen, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return False  # process exited early
            if is_port_open(port):
                return True
            time.sleep(0.2)
        return is_port_open(port)

    def stop_forward(self, udid: str) -> None:
        with self._lock:
            handle = self._handles.pop(udid, None)
        if not handle:
            return
        _log.info("stopping forward %s (port %d)", udid, handle.local_port)
        with suppress(OSError, subprocess.SubprocessError):
            handle.process.terminate()
        try:
            handle.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                handle.process.kill()

    def restart_forward(self, udid: str) -> ForwardHandle:
        handle = self._handles.get(udid)
        remote = handle.remote_port if handle else 8100
        local = handle.local_port if handle else None
        self.stop_forward(udid)
        return self.start_forward(udid, local_port=local, remote_port=remote)

    def is_alive(self, udid: str) -> bool:
        handle = self._handles.get(udid)
        return bool(handle and handle.process.poll() is None)

    def health_check(self, udid: str) -> bool:
        handle = self._handles.get(udid)
        if not handle or handle.process.poll() is not None:
            return False
        return is_port_open(handle.local_port)

    def local_port(self, udid: str) -> Optional[int]:
        handle = self._handles.get(udid)
        return handle.local_port if handle else None

    def stop_all(self) -> None:
        for udid in list(self._handles.keys()):
            self.stop_forward(udid)
