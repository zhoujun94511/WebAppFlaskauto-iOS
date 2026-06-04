"""UI automation HTTP API: element find/tap/type/pinch + foreground + source."""

from __future__ import annotations

from flask import Blueprint, request

from api import api
from services.ios_automation_service import IOSAutomationService
from services.runtime_state import state

bp = Blueprint("automation", __name__, url_prefix="/api/devices")
_svc = IOSAutomationService()


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.post("/<udid>/element/find")
@api
def find(udid: str):
    state.touch(udid)
    return _svc.find(udid, _body())


@bp.post("/<udid>/element/tap")
@api
def tap(udid: str):
    state.touch(udid)
    return _svc.tap(udid, _body())


@bp.post("/<udid>/element/type")
@api
def type_text(udid: str):
    state.touch(udid)
    return _svc.type_text(udid, _body())


@bp.post("/<udid>/element/pinch")
@api
def pinch(udid: str):
    state.touch(udid)
    return _svc.pinch(udid, _body())


@bp.get("/<udid>/foreground")
@api
def foreground(udid: str):
    state.touch(udid)
    return _svc.foreground(udid)


@bp.get("/<udid>/source")
@api
def source(udid: str):
    state.touch(udid)
    return _svc.source(udid)
