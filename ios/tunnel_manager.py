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
from typing import Tuple

from ios.go_ios import GoIOS
from utils.logging_setup import get_logger
from utils.port_utils import is_port_open, kill_listeners

_log = get_logger(__name__)

# go-ios's built-in "Go-iOS Agent" HTTP-API port. With ENABLE_GO_IOS_AGENT=user
# (which we set on every tunnel/dev command), go-ios ALWAYS spins up this default
# agent in addition to the one pinned via --tunnel-info-port -- so a single tunnel
# start leaves TWO agent processes (the pinned info-port one AND this 60105 one),
# each holding device tunnels. Our reclaim/stop must free BOTH ports or the 60105
# agent leaks across restarts and orphans accumulate.
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
        """(running, raw). Queries the PINNED info port so it doesn't spawn a
        second agent on a different port."""
        code, out, err = self.goios.run(
            ["tunnel", "ls", *self._port_flag()], timeout=15, agent=True
        )
        raw = (out or err or "").strip()
        low = raw.lower()
        if "not running" in low or "no connection could be made" in low or "refused" in low:
            return False, raw
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("[") or line.startswith("{"):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, list):
                    return len(data) > 0, raw
                if isinstance(data, dict):
                    return bool(data), raw
        return code == 0, raw

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
                    self._agent = self.goios.popen(
                        ["tunnel", "start", "--userspace", *self._port_flag()],
                        agent=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    return False, f"failed to spawn tunnel agent: {exc}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._agent and self._agent.poll() is not None:
                ok, raw = self.status()
                return ok, "tunnel ready" if ok else f"tunnel agent exited: {raw[:200]}"
            ok, _ = self.status()
            if ok:
                _log.info("go-ios userspace tunnel ready")
                return True, "tunnel ready"
            time.sleep(1.0)
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
