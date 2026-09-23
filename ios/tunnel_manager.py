"""go-ios userspace RSD tunnel lifecycle (iOS 17+, no admin on Windows).

The userspace tunnel agent is a long-lived background process. To avoid the
"each start spawns an orphan that conflicts on the port" problem, we:
  * PIN the agent's HTTP-API port (``--tunnel-info-port``) so it's
    deterministic and reclaimable;
  * RECLAIM stale agents on startup (``tunnel stopagent`` + kill any leftover
    listener on the pinned port);
  * REUSE a healthy agent instead of starting another;
  * track the agent process and STOP it on shutdown.

NOTE: an userspace tunnel is internal to go-ios's process -- pymobiledevice3
cannot route through it. So go-ios (not pymobiledevice3) owns the tunnel AND
the dev-service actions that need it (runwda). pymobiledevice3 keeps doing the
no-tunnel work (usbmux list/forward, WDA HTTP).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from ios.go_ios import GoIOS
from utils.logging_setup import get_logger
from utils.port_utils import is_port_open, kill_listeners

_log = get_logger(__name__)


def tunnels_listed(raw: str) -> Optional[bool]:
    """Whether ``tunnel ls`` output contains a non-empty tunnel list.

    go-ios prints a JSON warning on its own line (``agent is not running``)
    and the list on another. A warning object must not count as a tunnel.
    ``None`` means no list was present.
    """
    found: Optional[bool] = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, list):
            found = len(data) > 0
    return found


def _agent_log_tail(proc, limit: int = 8) -> str:
    path = getattr(proc, "_goios_log", None) if proc is not None else None
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])

# go-ios's default agent port. A leftover `ENABLE_GO_IOS_AGENT=user` process
# listens here and opens its own lockdown session beside the agent we pin to
# 28100. Reclaim it so only one tunnel talks to the phone.
_GOIOS_DEFAULT_AGENT_PORT = 60105


class TunnelManager:
    def __init__(self, goios: GoIOS):
        self.goios = goios
        self.info_port = goios.tunnel_info_port
        self._agent = None  # background Popen for `tunnel start --userspace`
        self._lock = threading.Lock()

    def _port_flag(self) -> list:
        return ["--tunnel-info-port", str(self.info_port)]

    def _reclaim_agent_ports(self) -> None:
        """Hard-kill any agent LISTENING on the pinned info port AND on go-ios's
        default agent port (60105). Killing each process also frees the
        device-tunnel ports (60106+) it owns. Best-effort; never raises."""
        for port in {self.info_port, _GOIOS_DEFAULT_AGENT_PORT}:
            if is_port_open(port):
                kill_listeners(port)

    def status(self) -> Tuple[bool, str]:
        """(running, raw). Query the pinned info port only.

        ``agent=False``: ENABLE_GO_IOS_AGENT would fork a second ``tunnel start``
        on port 60105 before this command runs, and that copy fights the agent
        we already started for the same lockdown session.
        """
        _code, out, err = self.goios.run(
            ["tunnel", "ls", *self._port_flag()], timeout=15, agent=False
        )
        raw = (out or err or "").strip()
        ready = tunnels_listed(raw)
        return bool(ready), raw

    def reclaim(self) -> None:
        """Startup cleanup: hard-kill a leftover agent listening on the pinned
        info port (orphan from a crash/hard-kill). Killing that one process
        also frees the device-tunnel ports (60106+) it owned, so a fresh
        ``tunnel start`` can bind the info port without a fatal conflict.

        NOTE: ``ios tunnel stopagent`` takes NO arguments and only targets the
        default agent (60105) -- it cannot stop an agent we pinned to a custom
        info port, so we reclaim by PID-on-port instead. We free both the pinned
        info port and go-ios's default 60105 agent (see ``_reclaim_agent_ports``).
        Safe to call when nothing is running."""
        with self._lock:
            self._reclaim_agent_ports()
            self._agent = None

    def ensure_running(self, timeout: float = 40.0) -> Tuple[bool, str]:
        ok, _ = self.status()
        if ok:
            return True, "tunnel already running"
        with self._lock:
            ok, _ = self.status()
            if ok:
                return True, "tunnel already running"
            # Concurrency guard (multi-device connect): if WE already started an
            # agent that's still alive, DON'T kill/restart it -- just fall through
            # to the wait loop so this caller shares the in-flight start. Killing
            # here would tear down the other device's not-yet-ready agent and
            # surface a spurious "tunnel agent exited".
            if self._agent is None or self._agent.poll() is not None:
                # Clean slate: hard-reclaim stale listeners on the pinned info
                # port AND the default 60105 agent (an orphan on the info port
                # makes `tunnel start` fatally fail to bind), then spawn fresh.
                self._reclaim_agent_ports()
                _log.info("starting go-ios userspace tunnel on port %d (no admin)...", self.info_port)
                try:
                    # agent=False so this process is the only tunnel. log_file
                    # keeps the handshake error off DEVNULL.
                    self._agent = self.goios.popen(
                        ["tunnel", "start", "--userspace", *self._port_flag()],
                        agent=False,
                        log_file=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    return False, f"failed to spawn tunnel agent: {exc}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._agent and self._agent.poll() is not None:
                ok, _raw = self.status()
                if ok:
                    return True, "tunnel ready"
                detail = _agent_log_tail(self._agent) or "tunnel agent exited"
                return False, detail
            ok, _ = self.status()
            if ok:
                _log.info("go-ios userspace tunnel ready")
                return True, "tunnel ready"
            time.sleep(1.0)
        detail = _agent_log_tail(self._agent)
        if detail:
            return False, f"tunnel did not become ready in time\n{detail}"
        return False, "tunnel did not become ready in time"

    def stop(self) -> None:
        with self._lock:
            if self._agent and self._agent.poll() is None:
                try:
                    self._agent.terminate()
                except OSError:
                    pass
            self._agent = None
            # The `tunnel start` wrapper we terminate may leave its child agents
            # (the pinned info-port one AND go-ios's default 60105 one, each
            # owning device tunnels) orphaned, so hard-kill by PID-on-port to
            # guarantee the next start can bind and no agent leaks.
            self._reclaim_agent_ports()
