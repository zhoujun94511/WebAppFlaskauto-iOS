"""Cross-platform subprocess runner used by the iOS adapter.

Centralizes how we shell out to ``pymobiledevice3`` (and friends) so every
call gets consistent timeout handling, logging (command / duration /
returncode) and a uniform :class:`CommandResult`. We never hardcode binary
paths — the executable is resolved from PATH / the active venv.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Sequence

from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


@dataclass
class CommandResult:
    args: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(
    args: Sequence[str],
    timeout: int = 15,
    check: bool = False,
) -> CommandResult:
    """Run ``args`` and return a :class:`CommandResult`.

    ``check=True`` raises :class:`AppError(INTERNAL_ERROR)` on non-zero exit.
    A timeout raises ``AppError`` too. ``FileNotFoundError`` (binary missing)
    surfaces as a specific error so callers can map it (e.g. pymobiledevice3
    not installed).
    """
    args = [str(a) for a in args]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"Executable not found: {args[0]}",
            {"args": args, "error": str(exc)},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        dur = int((time.perf_counter() - started) * 1000)
        _log.warning("command timed out after %dms: %s", dur, " ".join(args))
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"Command timed out after {timeout}s",
            {"args": args, "timeout": timeout},
        ) from exc

    dur = int((time.perf_counter() - started) * 1000)
    result = CommandResult(
        args=args,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_ms=dur,
    )
    _log.debug(
        "command rc=%s dur=%dms: %s", result.returncode, dur, " ".join(args)
    )
    if check and not result.ok:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"Command failed (rc={result.returncode}): {' '.join(args)}",
            {"stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]},
        )
    return result


def pymobiledevice3_cmd() -> List[str]:
    """Base argv to invoke pymobiledevice3 via the current interpreter.

    Using ``python -m pymobiledevice3`` (rather than a bare ``pymobiledevice3``
    on PATH) keeps us pinned to the active venv where the dep is installed.
    """
    return [sys.executable, "-m", "pymobiledevice3"]
