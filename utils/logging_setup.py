"""Structured-ish logging with an in-memory ring buffer.

Beyond the usual stdlib logger, we keep the last N records in a ring buffer
and expose a subscribe hook so the Socket.IO ``logs`` handler can stream
``log:entry`` events to the browser LogPanel without re-reading files.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from contextlib import suppress
from datetime import datetime
from pathlib import Path
import threading
from typing import Callable, Deque, List

# Per-run log file under ``logs/`` at the repo root — one file per backend
# start, ``app-YYYYMMDD-HHMMSS.log`` (borrowed from the Android sibling) so
# post-mortem debugging doesn't require having had the terminal open.
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Third-party loggers that are noisy but rarely actionable here. aioice (pulled
# in by aiortc/WebRTC) logs an INFO line per failed UDP bind on every virtual
# NIC during ICE gathering on Windows — quiet it to WARNING.
_QUIET_LOGGERS = {"aioice.ice": logging.WARNING, "aioice.turn": logging.WARNING}

# ANSI SGR colours per level so the console isn't a wall of red. Without explicit
# colour codes, terminals/IDEs paint ALL stderr red — making INFO look like an
# error. Plain (no codes) on non-TTY sinks so log FILES stay greppable.
_RESET = "\x1b[0m"
_LEVEL_COLOURS = {
    "DEBUG": "\x1b[36m",       # cyan
    "INFO": "\x1b[32m",        # green
    "WARNING": "\x1b[33m",     # yellow
    "ERROR": "\x1b[31m",       # red
    "CRITICAL": "\x1b[1;31m",  # bold red
}
# Pad level names to 5 chars so columns line up (WARNING→WARN, CRITICAL→CRIT).
_LEVEL_SHORT = {
    "DEBUG": "DEBUG", "INFO": "INFO ", "WARNING": "WARN ",
    "ERROR": "ERROR", "CRITICAL": "CRIT ",
}
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _AlignedFormatter(logging.Formatter):
    """Padded level tags with optional per-level ANSI colour.

    Colour only when ``use_color=True`` (a TTY). Mirrors the Android sibling's
    formatter so both projects read identically:
    ``2026-06-04 14:08:35 [INFO ] services.app: ...``
    """

    def __init__(self, *, use_color: bool, datefmt: str = _DATEFMT):
        super().__init__(datefmt=datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        record.message = record.getMessage()
        asctime = self.formatTime(record, self.datefmt)
        short = _LEVEL_SHORT.get(record.levelname, record.levelname.ljust(5)[:5])
        if self._use_color:
            colour = _LEVEL_COLOURS.get(record.levelname, "")
            level_tag = f"[{colour}{short}{_RESET}]"
        else:
            level_tag = f"[{short}]"
        line = f"{asctime} {level_tag} {record.name}: {record.message}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


# noinspection SpellCheckingInspection
def _stream_supports_color(stream) -> bool:
    """Best-effort TTY detection; honours NO_COLOR / FORCE_COLOR (isatty)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(getattr(stream, "isatty", lambda: False)())
    except (OSError, AttributeError, ValueError):
        return False

_MAX_RECORDS = 500
_buffer: Deque[dict] = deque(maxlen=_MAX_RECORDS)
_subscribers: List[Callable[[dict], None]] = []
_lock = threading.Lock()


class _RingHandler(logging.Handler):
    """Mirror every log record into the ring buffer + notify subscribers."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return
        with _lock:
            _buffer.append(entry)
            subs = list(_subscribers)
        for fn in subs:
            with suppress(Exception):
                fn(entry)


_configured = False


def _resolve_level(level) -> int:
    """LOG_LEVEL env overrides the passed default (DEBUG/INFO/WARNING/…)."""
    env = os.environ.get("LOG_LEVEL")
    if env:
        parsed = logging.getLevelName(env.strip().upper())
        if isinstance(parsed, int):
            return parsed
    return level


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    root.setLevel(_resolve_level(level))

    # Console: per-level ANSI colour on a TTY so INFO reads green (not red).
    stream = logging.StreamHandler()
    use_color = _stream_supports_color(stream.stream)
    if use_color and os.name == "nt":
        # Enable ANSI on Win10+ consoles so the codes render instead of leaking.
        # getattr avoids static-analysis "unresolved reference" noise for the
        # Win32 calls on non-Windows checkouts (this branch only runs on nt).
        with suppress(Exception):
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            get_std = getattr(kernel32, "GetStdHandle")
            set_mode = getattr(kernel32, "SetConsoleMode")
            set_mode(get_std(-11), 7)  # stdout
            set_mode(get_std(-12), 7)  # stderr
    stream.setFormatter(_AlignedFormatter(use_color=use_color))
    root.addHandler(stream)

    # Mirror everything to a per-run file under logs/ — always plain (no ANSI).
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = _LOG_DIR / f"app-{ts}.log"
        file_handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        file_handler.setFormatter(_AlignedFormatter(use_color=False))
        root.addHandler(file_handler)
        print(f"[logging] writing to {log_path}", file=sys.stderr)
    except OSError as exc:  # disk full / read-only — keep console + ring only
        print(f"[logging] file handler disabled: {exc}", file=sys.stderr)

    root.addHandler(_RingHandler())

    for name, lvl in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(lvl)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def recent_logs(limit: int = 200, level: str | None = None) -> List[dict]:
    with _lock:
        items = list(_buffer)
    if level:
        items = [e for e in items if e["level"] == level.upper()]
    return items[-limit:]


def subscribe(fn: Callable[[dict], None]) -> Callable[[], None]:
    with _lock:
        _subscribers.append(fn)

    def _off() -> None:
        with _lock:
            if fn in _subscribers:
                _subscribers.remove(fn)

    return _off
