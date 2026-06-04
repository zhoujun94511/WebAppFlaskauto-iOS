"""Control HTTP API: tap / swipe / input / screenshot / launch / terminate."""

from __future__ import annotations

from flask import Blueprint, request

from api import api
from services.ios_control_service import IOSControlService
from services.runtime_state import state
from services.request_validators import require

bp = Blueprint("control", __name__, url_prefix="/api/devices")
_svc = IOSControlService()


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.post("/<udid>/tap")
@api
def tap(udid: str):
    state.touch(udid)  # mark active so the idle reaper doesn't release it
    return _svc.tap(udid, _body())


@bp.post("/<udid>/swipe")
@api
def swipe(udid: str):
    state.touch(udid)
    return _svc.swipe(udid, _body())


@bp.post("/<udid>/longpress")
@api
def long_press(udid: str):
    state.touch(udid)
    return _svc.long_press(udid, _body())


@bp.post("/<udid>/doubletap")
@api
def double_tap(udid: str):
    state.touch(udid)
    return _svc.double_tap(udid, _body())


@bp.post("/<udid>/input")
@api
def input_text(udid: str):
    state.touch(udid)
    return _svc.input_text(udid, _body())


@bp.get("/<udid>/alert")
@api
def get_alert(udid: str):
    state.touch(udid)
    return _svc.alert(udid)


@bp.post("/<udid>/alert")
@api
def alert_action(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "action")
    return _svc.alert_action(udid, str(body["action"]))


@bp.post("/<udid>/button")
@api
def button(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "name")
    return _svc.button(udid, str(body["name"]))


@bp.post("/<udid>/key")
@api
def key(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "name")
    return _svc.key(udid, str(body["name"]))


@bp.post("/<udid>/accessibility")
@api
def accessibility(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "feature")
    return _svc.accessibility(udid, str(body["feature"]), str(body.get("action", "toggle")))


@bp.get("/<udid>/clipboard")
@api
def get_clipboard(udid: str):
    state.touch(udid)
    return _svc.get_clipboard(udid)


@bp.post("/<udid>/clipboard")
@api
def set_clipboard(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "text")
    return _svc.set_clipboard(udid, str(body["text"]))


@bp.post("/<udid>/screenshot")
@api
def screenshot(udid: str):
    state.touch(udid)
    return _svc.screenshot(udid, as_base64=True)


@bp.post("/<udid>/launch")
@api
def launch(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "bundle_id")
    return _svc.launch_app(udid, str(body["bundle_id"]))


@bp.post("/<udid>/terminate")
@api
def terminate(udid: str):
    state.touch(udid)
    body = _body()
    require(body, "bundle_id")
    return _svc.terminate_app(udid, str(body["bundle_id"]))
