"""Live iOS device syslog over HTTP SSE (mirrors the reference WebAppForIos).

A reservation owner opens ``GET /api/devices/<udid>/syslog``; we spawn the
bundled go-ios ``ios syslog --parse`` and yield its stdout as a
``text/event-stream``. The go-ios process is tied to the request: when the
client (EventSource) disconnects, the generator is closed and we terminate it.
A small registry lets device release tear down any active stream for a UDID.

This is the *device* log (the phone's own system log) — deliberately delivered
over plain HTTP SSE rather than Socket.IO so it never contends with the
Socket.IO frame/event channel.
"""

from __future__ import annotations

import json
import subprocess
import threading
from contextlib import suppress
from typing import Dict, List

from utils.logging_setup import get_logger

_log = get_logger(__name__)

_lock = threading.Lock()
_active: Dict[str, List[subprocess.Popen]] = {}  # udid -> live syslog procs


def _register(udid: str, proc: subprocess.Popen) -> None:
    with _lock:
        _active.setdefault(udid, []).append(proc)


def _unregister(udid: str, proc: subprocess.Popen) -> None:
    with _lock:
        lst = _active.get(udid)
        if lst and proc in lst:
            lst.remove(proc)
        if lst is not None and not lst:
            _active.pop(udid, None)


def stop_for_device(udid: str) -> None:
    """Terminate any live syslog stream(s) for a device (e.g. on release)."""
    with _lock:
        procs = list(_active.get(udid, []))
    for proc in procs:
        _terminate(proc)


def sse_stream(udid: str):
    """Generator yielding SSE frames of the device's live syslog."""
    from services import get_adapter

    try:
        proc = get_adapter().goios.syslog_popen(udid)
    except Exception as exc:  # noqa: BLE001 — report to the client, don't 500
        _log.exception("syslog spawn failed for %s", udid)
        yield f"event: error\ndata: {exc}\n\n"
        return

    _register(udid, proc)
    _log.info("device syslog SSE started: %s", udid[:12])
    try:
        yield "retry: 10000\n: connected\n\n"  # open the stream; cap auto-retry
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line:
                yield f"data: {_readable(line)}\n\n"
    except GeneratorExit:
        pass  # client (EventSource) went away
    finally:
        _terminate(proc)
        _unregister(udid, proc)
        _log.info("device syslog SSE stopped: %s", udid[:12])


def _readable(line: str) -> str:
    """go-ios ``--parse`` emits each record as JSON (``{"msg": "...", ...}``);
    show the human-readable ``msg`` (also decodes the \\uXXXX escapes). Plain
    lines pass through unchanged."""
    if line.startswith("{"):
        with suppress(ValueError):
            obj = json.loads(line)
            return obj.get("msg") or obj.get("message") or line
    return line


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        with suppress(Exception):
            proc.kill()
