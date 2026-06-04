"""Local TCP port helpers for WDA forwarding."""

from __future__ import annotations

import socket
from contextlib import closing


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(start: int, end: int | None = None, host: str = "127.0.0.1") -> int:
    """Return the first free port >= ``start`` (scanning up to ``end``)."""
    end = end or (start + 200)
    for port in range(start, end + 1):
        if is_port_free(port, host):
            return port
    raise OSError(f"No free local port in range {start}-{end}")


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if something is LISTENING on host:port (i.e. the forward is up)."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def kill_listeners(port: int) -> list[int]:
    """Kill any process LISTENING on ``port``; return the PIDs killed.

    Used to reclaim a stale go-ios tunnel agent orphaned by a crash/hard-kill
    (its pinned info port would otherwise cause a bind conflict). Best-effort,
    cross-platform; never raises.
    """
    from utils.logging_setup import get_logger

    log = get_logger(__name__)
    killed: list[int] = []
    try:
        import psutil  # available transitively (pymobiledevice3)

        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.status == psutil.CONN_LISTEN
                and conn.pid
            ):
                try:
                    psutil.Process(conn.pid).kill()
                    killed.append(conn.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception as exc:  # noqa: BLE001 — psutil missing / permission, etc.
        log.debug("kill_listeners(%d) fallback skipped: %s", port, exc)
    if killed:
        log.info("reclaimed stale listener(s) on port %d: pids=%s", port, killed)
    return killed
