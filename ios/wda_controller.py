"""Thin client for WebDriverAgent's HTTP API (no Appium server in the loop).

WDA runs on the device and is reached through the local forwarded port. We
manage a session lazily, and every failed request raises AppError with the
endpoint / status / body so the UI can show something actionable.
"""

from __future__ import annotations

import base64
import binascii
import threading
from contextlib import suppress
from typing import Optional, Tuple

import httpx

from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


class WDAController:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    # ── low-level ────────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs):
        url = path if path.startswith("/") else f"/{path}"
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.WDA_NOT_RUNNING,
                "WebDriverAgent is not reachable",
                {"endpoint": url, "base_url": self.base_url, "error": str(exc)},
            ) from exc
        if resp.status_code >= 400:
            raise AppError(
                ErrorCode.WDA_REQUEST_FAILED,
                f"WDA request failed: {method} {url} -> {resp.status_code}",
                {
                    "endpoint": url,
                    "status_code": resp.status_code,
                    "response_text": resp.text[:800],
                },
            )
        return resp

    def _json(self, method: str, path: str, **kwargs) -> dict:
        resp = self._request(method, path, **kwargs)
        try:
            return resp.json()
        except ValueError:
            return {}

    # ── status / session ─────────────────────────────────────────────
    def status(self) -> dict:
        return self._json("GET", "/status")

    def health_check(self) -> bool:
        try:
            data = self.status()
            return bool(data.get("value") or data.get("sessionId") or data)
        except AppError:
            return False

    def create_session(self, force: bool = False) -> str:
        with self._lock:
            if self._session_id and not force:
                return self._session_id
            # Capabilities tuned for responsiveness: don't wait for the app to be
            # "quiescent" (the default stalls every action while animations — e.g.
            # a swipe's scroll — settle, which also stalls the MJPEG capture).
            caps = {
                "shouldWaitForQuiescence": False,
                "shouldUseCompactResponses": True,
                "waitForIdleTimeout": 0,
                "maxTypingFrequency": 60,
            }
            payload = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}}
            data = self._json("POST", "/session", json=payload)
            sid = (
                data.get("sessionId")
                or (data.get("value") or {}).get("sessionId")
                or ""
            )
            self._session_id = sid
            return sid

    def _session(self) -> str:
        return self._session_id or self.create_session()

    def _session_request(self, method: str, subpath: str, **kwargs):
        """Request under ``/session/<id>``; if WDA reports the session is gone
        (404 — runner restarted / session timed out), recreate it once and
        retry. Prevents a stale cached session id from turning every tap/swipe
        into a 404 flood until a full reconnect."""
        sid = self._session()
        try:
            return self._request(method, f"/session/{sid}{subpath}", **kwargs)
        except AppError as exc:
            if exc.code != ErrorCode.WDA_REQUEST_FAILED or exc.detail.get("status_code") != 404:
                raise
            _log.info("WDA session stale (404) — recreating and retrying %s", subpath)
            sid = self.create_session(force=True)
            return self._request(method, f"/session/{sid}{subpath}", **kwargs)

    def _session_json(self, method: str, subpath: str, **kwargs) -> dict:
        resp = self._session_request(method, subpath, **kwargs)
        try:
            return resp.json()
        except ValueError:
            return {}

    def update_settings(self, settings: dict) -> None:
        """POST WDA appium settings (e.g. mjpegServerFramerate/Quality).

        Enables/tunes WDA's MJPEG broadcaster (device port 9100). Best-effort:
        a failure here must not block control — callers swallow it.
        """
        self._session_request("POST", "/appium/settings", json={"settings": settings})

    # ── input ────────────────────────────────────────────────────────
    def tap(self, x: int, y: int) -> None:
        # W3C Actions pointer sequence (works across recent WDA builds).
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 60},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._session_request("POST", "/actions", json=actions)

    def long_press(self, x: int, y: int, duration: float = 0.8) -> None:
        ms = max(1, int(duration * 1000))
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": ms},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._session_request("POST", "/actions", json=actions)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        ms = max(1, int(duration * 1000))
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": ms, "x": int(x2), "y": int(y2)},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._session_request("POST", "/actions", json=actions)

    def input_text(self, text: str) -> None:
        self._session_request("POST", "/wda/keys", json={"value": list(text)})

    # ── hardware buttons ─────────────────────────────────────────────
    def press_button(self, name: str) -> None:
        """Hardware button by WDA name: home / volumeUp / volumeDown / snapshot.
        Session-scoped in WDA (the top-level route 404s)."""
        self._session_request("POST", "/wda/pressButton", json={"name": name})

    def home(self) -> None:
        # Via pressButton (reliable); the top-level /wda/homescreen 500s on some
        # builds.
        self.press_button("home")

    def lock(self) -> None:
        self._request("POST", "/wda/lock")

    def unlock(self) -> None:
        self._request("POST", "/wda/unlock")

    # ── pasteboard / clipboard ───────────────────────────────────────
    def set_pasteboard(self, text: str) -> None:
        """Write text to the device clipboard (works in background)."""
        content = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._session_request("POST", "/wda/setPasteboard",
                              json={"content": content, "contentType": "plaintext"})

    def get_pasteboard(self) -> str:
        """Read the device clipboard. NOTE: iOS only allows this when WDA is in
        the foreground; in the background it returns empty (an OS restriction,
        not a failure)."""
        resp = self._session_request("POST", "/wda/getPasteboard",
                                     json={"contentType": "plaintext"})
        try:
            b64 = (resp.json() or {}).get("value") or ""
        except ValueError:
            return ""
        try:
            return base64.b64decode(b64).decode("utf-8", "replace") if b64 else ""
        except (binascii.Error, ValueError):
            return ""

    def double_tap(self, x: int, y: int) -> None:
        self._session_request("POST", "/wda/doubleTap", json={"x": int(x), "y": int(y)})

    def active_app_info(self) -> dict:
        """Foreground app: {bundleId, pid, name}."""
        try:
            return (self._request("GET", "/wda/activeAppInfo").json() or {}).get("value") or {}
        except AppError:
            return {}

    # ── element selectors (accessibility id / predicate / xpath / …) ──
    # Map friendly strategy names → WDA's W3C "using" strings.
    _USING = {
        "id": "accessibility id", "accessibility_id": "accessibility id",
        "name": "name", "class": "class name", "class_name": "class name",
        "predicate": "predicate string", "classchain": "class chain",
        "class_chain": "class chain", "xpath": "xpath",
    }

    def find_element(self, using: str, value: str) -> Optional[str]:
        """Return the element UUID, or None if not found. Uses a direct request
        (not the session-recovery wrapper) so a 'no such element' doesn't churn
        the WDA session."""
        sid = self._session()
        strat = self._USING.get((using or "").strip().lower(), using)
        try:
            resp = self._request("POST", f"/session/{sid}/element",
                                 json={"using": strat, "value": value})
        except AppError:
            return None
        val = (resp.json() or {}).get("value") or {}
        return val.get("ELEMENT") or val.get("element-6066-11e4-a52e-4f735466cecf")

    def element_info(self, uuid: str) -> dict:
        def g(path):
            try:
                return self._session_json("GET", f"/element/{uuid}{path}").get("value")
            except AppError:
                return None
        return {
            "uuid": uuid,
            "label": g("/attribute/label"),
            "value": g("/attribute/value"),
            "name": g("/attribute/name"),
            "displayed": g("/displayed"),
            "rect": g("/rect"),
        }

    def click_element(self, uuid: str) -> None:
        self._session_request("POST", f"/element/{uuid}/click")

    def set_element_value(self, uuid: str, text: str) -> None:
        self._session_request("POST", f"/element/{uuid}/value", json={"value": list(text)})

    def clear_element(self, uuid: str) -> None:
        self._session_request("POST", f"/element/{uuid}/clear")

    def pinch_element(self, uuid: str, scale: float, velocity: float = 1.0) -> None:
        self._session_request("POST", f"/wda/element/{uuid}/pinch",
                              json={"scale": scale, "velocity": velocity})

    # ── alerts (system popups) ───────────────────────────────────────
    def alert_text(self) -> Optional[str]:
        """Current alert's text, or None if no alert is showing."""
        try:
            data = self._session_json("GET", "/alert/text")
        except AppError:
            return None
        val = data.get("value")
        return str(val) if val not in (None, "") else None

    def alert_buttons(self) -> list:
        try:
            return list((self._session_json("GET", "/alert/buttons") or {}).get("value") or [])
        except AppError:
            return []

    def alert_accept(self) -> None:
        self._session_request("POST", "/alert/accept")

    def alert_dismiss(self) -> None:
        self._session_request("POST", "/alert/dismiss")

    # ── screen ───────────────────────────────────────────────────────
    def screenshot(self) -> bytes:
        """Return raw PNG bytes of the current screen."""
        data = self._json("GET", "/screenshot")
        b64 = data.get("value") if isinstance(data, dict) else None
        if not b64:
            raise AppError(
                ErrorCode.SCREENSHOT_FAILED, "WDA screenshot returned no data", {}
            )
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise AppError(
                ErrorCode.SCREENSHOT_FAILED, "Bad base64 in WDA screenshot", {}
            ) from exc

    def get_window_size(self) -> Tuple[int, int]:
        data = self._session_json("GET", "/window/size")
        val = data.get("value") or {}
        return int(val.get("width", 0)), int(val.get("height", 0))

    def get_orientation(self) -> str:
        data = self._session_json("GET", "/orientation")
        return str(data.get("value") or "PORTRAIT")

    def source(self) -> str:
        data = self._session_json("GET", "/source")
        return str(data.get("value") or "")

    # ── apps ─────────────────────────────────────────────────────────
    def launch_app(self, bundle_id: str) -> None:
        self._session_request("POST", "/wda/apps/launch", json={"bundleId": bundle_id})

    def terminate_app(self, bundle_id: str) -> None:
        self._session_request("POST", "/wda/apps/terminate", json={"bundleId": bundle_id})

    def close(self) -> None:
        with suppress(Exception):
            self._client.close()
