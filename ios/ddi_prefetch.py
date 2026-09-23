"""Download the Developer Disk Image file before the user clicks Connect.

iOS 17+ uses one shared personalized image (pymobiledevice3's
``LATEST_DDI_BUILD_ID``), not a file named after this phone's ProductVersion.
Fetching that file only needs the network. Mounting it still happens on
connect, because the phone has to issue a nonce for its own board and chip.
"""

from __future__ import annotations

import threading

from utils.logging_setup import get_logger

_log = get_logger(__name__)
_lock = threading.Lock()
_inflight = False
_done = False


def schedule_personalized_ddi_prefetch() -> None:
    """Start one background download if this process has not tried yet."""
    global _inflight
    with _lock:
        if _done or _inflight:
            return
        _inflight = True
    threading.Thread(target=_run, name="ddi-prefetch", daemon=True).start()


def _run() -> None:
    global _inflight, _done
    try:
        from pymobiledevice3.services.mobile_image_mounter import (
            LATEST_DDI_BUILD_ID,
            fetch_personalized_ddi,
        )

        _image, manifest, _trust = fetch_personalized_ddi()
        _log.info(
            "personalized DDI cache ready (%s) at %s",
            LATEST_DDI_BUILD_ID,
            manifest.parent,
        )
    except Exception as exc:  # noqa: BLE001 — prefetch must not break discovery
        _log.warning("personalized DDI prefetch failed: %s", exc)
    finally:
        with _lock:
            _inflight = False
            _done = True


def reset_prefetch_for_tests() -> None:
    global _inflight, _done
    with _lock:
        _inflight = False
        _done = False
