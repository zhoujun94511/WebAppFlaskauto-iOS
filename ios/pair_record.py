"""Install a finished lockdown pair record that never replaced the live file.

On Windows, Apple Mobile Device writes ``<udid>.plist.tmp`` and then renames
it onto ``<udid>.plist``. When that rename does not happen, go-ios keeps
sending the old HostID and the device answers ``InvalidHostID``.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from utils.logging_setup import get_logger

_log = get_logger(__name__)

_REQUIRED = ("HostID", "SystemBUID", "HostCertificate", "HostPrivateKey")


def lockdown_dir() -> Path:
    root = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(root) / "Apple" / "Lockdown"


def pymobiledevice3_pair_dir() -> Path:
    return Path.home() / ".pymobiledevice3"


def promote_pending_pair_record(
    udid: str,
    folder: Path | None = None,
    pmd3_dir: Path | None = None,
) -> bool:
    """Replace a stale ``<udid>.plist`` with a newer complete ``.plist.tmp``.

    Returns True when the live record changed. Also copies it to
    pymobiledevice3's cache when that directory already exists, so the two
    stacks do not pair the device again under a different HostID.
    """
    folder = folder if folder is not None else lockdown_dir()
    pending = folder / f"{udid}.plist.tmp"
    official = folder / f"{udid}.plist"
    if not pending.is_file():
        return False
    try:
        pending_data = plistlib.loads(pending.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    if any(not pending_data.get(key) for key in _REQUIRED):
        return False
    if official.is_file():
        try:
            current = plistlib.loads(official.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            current = {}
        if current.get("HostID") == pending_data.get("HostID"):
            return False
        if pending.stat().st_mtime < official.stat().st_mtime:
            return False
        backup = official.with_name(official.name + ".bak")
        official.replace(backup)
    pending.replace(official)
    _sync_pmd3(udid, official, pmd3_dir if pmd3_dir is not None else pymobiledevice3_pair_dir())
    _log.info("installed newer lockdown pair record for %s", udid)
    return True


def _sync_pmd3(udid: str, official: Path, pmd3_dir: Path) -> None:
    if not pmd3_dir.is_dir():
        return
    target = pmd3_dir / f"{udid}.plist"
    target.write_bytes(official.read_bytes())
