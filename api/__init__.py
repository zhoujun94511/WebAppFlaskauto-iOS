"""HTTP API blueprints + a decorator enforcing the unified response envelope.

API functions return either a plain value (wrapped as ``data``) or a dict
already shaped as the success envelope. AppError → failure envelope with the
mapped HTTP status; unexpected exceptions → INTERNAL_ERROR (500).
"""

from __future__ import annotations

import functools
from typing import Callable

from flask import jsonify

from utils.app_errors import AppError, ErrorCode, ok
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def api(fn: Callable):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            # Allow handlers to return (payload, status) or a raw Response.
            if isinstance(result, tuple):
                return result
            if isinstance(result, dict) and "success" in result:
                return jsonify(result)
            return jsonify(ok(result))
        except AppError as exc:
            _log.info("API AppError %s: %s", exc.code, exc.message)
            return jsonify(exc.to_dict()), exc.http_status
        except Exception as exc:  # noqa: BLE001
            _log.exception("Unhandled API error in %s", fn.__name__)
            return (
                jsonify(
                    {
                        "success": False,
                        "code": ErrorCode.INTERNAL_ERROR,
                        "message": str(exc),
                        "detail": {},
                    }
                ),
                500,
            )

    return wrapper


def register_blueprints(app) -> None:
    from api.health_api import bp as health_bp
    from api.devices_api import bp as devices_bp
    from api.control_api import bp as control_bp
    from api.streams_api import bp as streams_bp
    from api.apps_api import bp as apps_bp
    from api.files_api import bp as files_bp
    from api.automation_api import bp as automation_bp
    from api.auth_api import bp as auth_bp
    from api.reservations_api import bp as reservations_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(reservations_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(control_bp)
    app.register_blueprint(streams_bp)
    app.register_blueprint(apps_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(automation_bp)
