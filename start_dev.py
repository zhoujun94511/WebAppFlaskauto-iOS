"""Cross-platform dev launcher for the Flask backend and Vite frontend."""

from __future__ import annotations

import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from contextlib import suppress

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"

IS_WINDOWS = os.name == "nt"
BACKEND_PORT = int(os.environ.get("PORT", "5001"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "5173"))
GOIOS_TUNNEL_PORT = int(os.environ.get("IOS_GOIOS_TUNNEL_INFO_PORT", "28100"))

_PRINT_LOCK = threading.Lock()
_STARTED_PROCESSES: list[subprocess.Popen[Any]] = []
_SHUTDOWN_DONE = False
_LAST_SIGNAL_AT = 0.0
_DOUBLE_TAP_WINDOW_S = 2.0
# Window after a shutdown signal during which a child exit is treated as part of
# the intentional teardown (not a crash) — covers Ctrl+C / IDE-stop reaching the
# children before this supervisor has finished tearing down.
_SHUTDOWN_GRACE_S = 5.0


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, TypeError, ValueError):
    pass


def _print(msg: str) -> None:
    with _PRINT_LOCK:
        print(f"[start_dev] {msg}", flush=True)


def _pids_on_port(port: int) -> list[str]:
    pids: list[str] = []
    try:
        if IS_WINDOWS:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pids.append(line.split()[-1])
        else:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True).stdout
            pids = [p for p in out.split() if p]
    except (OSError, subprocess.SubprocessError) as exc:
        _print(f"could not inspect port {port}: {exc}")
    return sorted(set(pids))


def _pid_is_running(pid: int) -> bool:
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True

    try:
        probe = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=4.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    output = f"{probe.stdout}\n{probe.stderr}"
    return re.search(rf"\b{pid}\b", output) is not None


def _lsof_owners(port: int) -> list[int]:
    owners: list[int] = []
    try:
        completed = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return owners
    for raw in completed.stdout.split():
        try:
            pid = int(raw.strip())
        except ValueError:
            continue
        if pid > 0 and pid not in owners:
            owners.append(pid)
    return owners


def _netstat_owners(port: int) -> list[int]:
    owners: list[int] = []
    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return owners

    token = f":{port}"
    for line in completed.stdout.splitlines():
        if token not in line:
            continue
        parts = [p for p in line.split() if p]
        if len(parts) < 5:
            continue
        if not any(p.endswith(token) for p in parts):
            continue
        if not any(p in {"LISTENING", "监听"} for p in parts):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in owners:
            owners.append(pid)
    return owners


def get_port_owners(port: int) -> list[int]:
    if not IS_WINDOWS:
        return [pid for pid in _lsof_owners(port) if _pid_is_running(pid)]

    owners: list[int] = []
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    f"$ErrorActionPreference='SilentlyContinue'; "
                    f"Get-NetTCPConnection -LocalPort {port} -State Listen "
                    f"| Select-Object -ExpandProperty OwningProcess"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=6.0,
        )
        if completed.returncode == 0:
            for raw in completed.stdout.splitlines():
                try:
                    pid = int(raw.strip())
                except ValueError:
                    continue
                if pid > 0 and pid not in owners and _pid_is_running(pid):
                    owners.append(pid)
    except (subprocess.TimeoutExpired, OSError):
        pass

    if owners:
        return owners
    return _netstat_owners(port)


def _posix_kill_pid(pid: int, port: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        _print(f"could not stop PID {pid} on port {port}: {exc}")
        return

    for _ in range(10):
        if not _pid_is_running(pid):
            _print(f"freed port {port}: stopped PID {pid}")
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
        _print(f"freed port {port}: force-killed PID {pid}")
    except (ProcessLookupError, OSError):
        pass


def stop_port_owner(port: int) -> None:
    for pid in get_port_owners(port):
        if not _pid_is_running(pid):
            continue
        if not IS_WINDOWS:
            _posix_kill_pid(pid, port)
            continue
        try:
            proc = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
                timeout=6.0,
            )
            if proc.returncode == 0:
                _print(f"freed port {port}: stopped PID {pid}")
            else:
                _print(
                    f"taskkill failed for PID {pid} on port {port}: "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )
        except subprocess.TimeoutExpired:
            _print(f"taskkill timed out for PID {pid} on port {port}; skipping")
        except OSError as exc:
            _print(f"could not stop PID {pid} on port {port}: {exc}")


def reset_port(port: int) -> None:
    for _retry in range(8):
        owners = [pid for pid in get_port_owners(port) if _pid_is_running(pid)]
        if not owners:
            return
        for _ in owners:
            stop_port_owner(port)
        time.sleep(0.8)
    remaining = [pid for pid in get_port_owners(port) if _pid_is_running(pid)]
    if remaining:
        raise RuntimeError(f"port {port} is still held by PID(s): {', '.join(map(str, remaining))}")


def _service_tag(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"


def _explain_exit_code(rc: int) -> str:
    if rc == 0:
        return "clean exit"
    if rc in (4294967295, -1):
        return "terminated rather than crashing"
    if rc < 0:
        return f"killed by signal {-rc}"
    return "see output above"


def _process_kwargs() -> dict[str, Any]:
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _start_process(
    name: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[Any]:
    _print(f"starting {name}...")
    # INHERIT the parent's stdout/stderr (don't PIPE). Capturing into a pipe
    # without a reader thread (a) hid all backend/frontend logs and (b) would
    # deadlock the child once the ~64KB pipe buffer filled — it blocks on
    # write. Inheriting streams child logs straight to this console, live.
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        **_process_kwargs(),
    )
    _STARTED_PROCESSES.append(proc)
    return proc


def _kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if not IS_WINDOWS:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = None
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
        return

    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
            timeout=6.0,
        )
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def kill_started_processes() -> None:
    global _SHUTDOWN_DONE
    if _SHUTDOWN_DONE:
        return
    _SHUTDOWN_DONE = True
    for proc in reversed(_STARTED_PROCESSES):
        _kill_process_tree(proc)


def _install_signal_handlers() -> None:
    def _handler(signum: int, _frame: object) -> None:
        global _LAST_SIGNAL_AT
        now = time.monotonic()
        if signum == signal.SIGINT and _STARTED_PROCESSES and (now - _LAST_SIGNAL_AT) > _DOUBLE_TAP_WINDOW_S:
            _LAST_SIGNAL_AT = now
            _print(
                f"Received signal {signum}. Press Ctrl+C again within "
                f"{_DOUBLE_TAP_WINDOW_S:.0f}s to shut down."
            )
            return
        _print(f"Received signal {signum}, shutting down...")
        kill_started_processes()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _resolve_node_exe() -> str | None:
    return shutil.which("node")


def stop_mode() -> int:
    _print("Cleaning local service ports...")
    reset_port(BACKEND_PORT)
    reset_port(FRONTEND_PORT)
    reset_port(GOIOS_TUNNEL_PORT)
    _print("Cleanup completed.")
    return 0


def start_mode(args: argparse.Namespace) -> int:
    backend_py = ROOT / "app.py"
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not backend_py.exists():
        raise FileNotFoundError(f"Missing backend entry point: {backend_py}")
    if not vite_js.exists():
        raise FileNotFoundError(f"Missing Vite ({vite_js}). Run `npm install` inside {FRONTEND_DIR} first.")

    reset_port(BACKEND_PORT)
    reset_port(FRONTEND_PORT)

    atexit.register(kill_started_processes)
    _install_signal_handlers()

    host = "127.0.0.1" if args.local_only else "0.0.0.0"

    backend_env = os.environ.copy()
    backend_env.update(
        {
            "HOST": host,
            "PORT": str(BACKEND_PORT),
            "PYTHONUNBUFFERED": "1",
            "OPEN_BROWSER": "0",
        }
    )

    backend = _start_process("backend", [sys.executable, str(backend_py)], ROOT, backend_env)

    frontend_env = os.environ.copy()
    frontend_env["PYTHONUNBUFFERED"] = "1"
    node_exe = _resolve_node_exe() or "node"
    frontend = _start_process(
        "frontend",
        [node_exe, str(vite_js), "--host", host, "--port", str(FRONTEND_PORT), "--strictPort"],
        FRONTEND_DIR,
        frontend_env,
    )

    if not args.no_browser:
        time.sleep(2.0)
        with suppress(Exception):
            webbrowser.open_new_tab(f"http://127.0.0.1:{FRONTEND_PORT}")

    _print("Ctrl+C to stop both.")
    try:
        while True:
            for name, proc in (("backend", backend), ("frontend", frontend)):
                rc = proc.poll()
                if rc is None:
                    continue
                # A clean exit (rc == 0) means the child chose to stop — not a
                # crash. Likewise, if we just received a shutdown signal (Ctrl+C
                # also reaches the children, which then exit), this is an
                # intentional teardown. In both cases stop the stack quietly.
                # Only a NON-zero exit with no recent signal is a real crash.
                signaled = _SHUTDOWN_DONE or (time.monotonic() - _LAST_SIGNAL_AT) < _SHUTDOWN_GRACE_S
                if rc == 0 or signaled:
                    _print(f"{name} exited (code {rc}); stopping the dev stack.")
                    return 0
                raise RuntimeError(f"{name} exited unexpectedly with code {rc} ({_explain_exit_code(rc)})")
            time.sleep(1.0)
    finally:
        kill_started_processes()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start or stop the dev stack.")
    parser.add_argument("command", nargs="?", choices=("start", "stop"), default="start")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the client in a browser.")
    parser.add_argument("--local-only", action="store_true", help="Bind backend/client to 127.0.0.1 instead of 0.0.0.0.")
    parser.add_argument("--verbose-logs", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lan", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "stop":
        return stop_mode()
    return start_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
