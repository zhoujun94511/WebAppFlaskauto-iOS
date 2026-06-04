"""WDAController: request building + error mapping, via httpx MockTransport."""

import base64
import json

import httpx
import pytest

from ios.wda_controller import WDAController
from utils.app_errors import AppError, ErrorCode

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
_captured = {}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    _captured["last_path"] = path
    if path == "/status":
        return httpx.Response(200, json={"value": {"ready": True}})
    if path == "/session" and request.method == "POST":
        return httpx.Response(200, json={"sessionId": "sess-1", "value": {}})
    if path.endswith("/actions"):
        _captured["actions"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"value": None})
    if path == "/screenshot":
        return httpx.Response(200, json={"value": _PNG})
    if path.endswith("/wda/keys"):
        _captured["keys"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"value": None})
    if path.endswith("/window/size"):
        return httpx.Response(200, json={"value": {"width": 390, "height": 844}})
    return httpx.Response(404, json={"value": {"error": "no route"}})


def _controller():
    c = WDAController("http://127.0.0.1:8100")
    c._client = httpx.Client(
        base_url="http://127.0.0.1:8100", transport=httpx.MockTransport(_handler)
    )
    return c


def test_status_and_health():
    c = _controller()
    assert c.health_check() is True
    assert "ready" in str(c.status())


def test_tap_builds_pointer_action():
    c = _controller()
    c.tap(100, 200)
    seq = _captured["actions"]["actions"][0]["actions"]
    move = next(a for a in seq if a["type"] == "pointerMove")
    assert move["x"] == 100 and move["y"] == 200
    assert any(a["type"] == "pointerDown" for a in seq)
    assert any(a["type"] == "pointerUp" for a in seq)


def test_swipe_has_two_moves():
    c = _controller()
    c.swipe(10, 20, 30, 40, duration=0.5)
    seq = _captured["actions"]["actions"][0]["actions"]
    moves = [a for a in seq if a["type"] == "pointerMove"]
    assert len(moves) == 2
    assert moves[-1]["x"] == 30 and moves[-1]["y"] == 40


def test_input_text_sends_chars():
    c = _controller()
    c.input_text("hi")
    assert _captured["keys"]["value"] == ["h", "i"]


def test_screenshot_decodes_bytes():
    c = _controller()
    data = c.screenshot()
    assert data.startswith(b"\x89PNG")


def test_request_failure_maps_to_apperror():
    def fail(_req):
        return httpx.Response(500, json={"value": {"error": "boom"}})

    c = WDAController("http://127.0.0.1:8100")
    c._client = httpx.Client(
        base_url="http://127.0.0.1:8100", transport=httpx.MockTransport(fail)
    )
    with pytest.raises(AppError) as ei:
        c.status()
    assert ei.value.code == ErrorCode.WDA_REQUEST_FAILED


def test_stale_session_404_recreates_and_retries():
    """A dead session (404 on /session/<id>/actions) must trigger one session
    recreate + retry, not bubble up as a failure."""
    st = {"sessions": 0, "actions_calls": []}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/session" and req.method == "POST":
            st["sessions"] += 1
            return httpx.Response(200, json={"sessionId": f"fresh-{st['sessions']}"})
        if path.endswith("/actions"):
            st["actions_calls"].append(path)
            # The stale cached session → 404; a freshly created one → 200.
            return httpx.Response(404 if "/session/dead/" in path else 200,
                                  json={"value": None})
        return httpx.Response(404, json={})

    c = WDAController("http://127.0.0.1:8100")
    c._client = httpx.Client(base_url="http://127.0.0.1:8100",
                             transport=httpx.MockTransport(handler))
    c._session_id = "dead"  # pretend we cached a now-dead session
    c.tap(5, 5)  # must NOT raise
    assert st["sessions"] == 1  # exactly one recreate happened
    assert c._session_id == "fresh-1"
    assert any("/session/fresh-1/" in p for p in st["actions_calls"])  # retried on new sid


def test_unreachable_maps_to_not_running():
    def conn_err(_req):
        raise httpx.ConnectError("refused")

    c = WDAController("http://127.0.0.1:8100")
    c._client = httpx.Client(
        base_url="http://127.0.0.1:8100", transport=httpx.MockTransport(conn_err)
    )
    with pytest.raises(AppError) as ei:
        c.status()
    assert ei.value.code == ErrorCode.WDA_NOT_RUNNING
