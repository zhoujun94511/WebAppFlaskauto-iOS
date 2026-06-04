"""Device reservation HTTP API: list / claim / release."""

from __future__ import annotations

from flask import Blueprint, request

from api import api
from services import auth_service as auth
from services import reservation_service as reservations
from services.request_validators import require
from utils.app_errors import AppError, ErrorCode

bp = Blueprint("reservations", __name__, url_prefix="/api/reservations")


def _public(res: dict, me: dict) -> dict:
    return {
        "device_id": res["device_id"], "username": res["username"],
        "expires_at": res["expires_at"], "is_mine": res["user_id"] == me["id"],
    }


@bp.get("")
@api
@auth.login_required
def list_reservations():
    me = auth.current_user()
    return {
        "reservations": [_public(r, me) for r in reservations.list_all()],
        "max_minutes": reservations.MAX_RESERVATION_MINUTES,
        "default_minutes": reservations.DEFAULT_RESERVATION_MINUTES,
    }


@bp.post("")
@api
@auth.login_required
def claim():
    body = request.get_json(silent=True) or {}
    require(body, "device_id")
    me = auth.current_user()
    try:
        res = reservations.claim(
            str(body["device_id"]), me,
            int(body.get("minutes") or reservations.DEFAULT_RESERVATION_MINUTES),
        )
    except reservations.ReservationError as exc:
        raise AppError(ErrorCode.CONFLICT, str(exc))
    return {"reservation": _public(res, me)}


@bp.delete("/<device_id>")
@api
@auth.login_required
def release(device_id: str):
    me = auth.current_user()
    res = reservations.get(device_id)
    # Owner or admin may release; others are denied (unless it's already free).
    if res and res["user_id"] != me["id"] and not auth.is_admin(me):
        raise AppError(ErrorCode.FORBIDDEN, f"设备已被 {res['username']} 占用")
    reservations.release(device_id,
                         reason="force_released" if (res and res["user_id"] != me["id"]) else "released")
    return {"message": "ok"}
