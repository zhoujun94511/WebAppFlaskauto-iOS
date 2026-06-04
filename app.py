"""WebAppFlaskauto-iOS 鈥?Flask + Flask-SocketIO entry point.

Mirrors the Android WebAppFlaskscrcpy backend conventions:
  * Socket.IO with ``async_mode="threading"`` (NOT eventlet/gevent) so a
    future aiortc/WebRTC layer keeps real OS sockets and asyncio works.
  * Thin API blueprints, business logic in services/, platform specifics
    behind ios/adapter.py.
  * Serves the built Vue SPA from frontend/dist; CORS open in dev.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from utils.logging_setup import get_logger, setup_logging

setup_logging()
_log = get_logger("app")

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "frontend" / "dist"


def load_config() -> dict:
    """Load config from .env (if python-dotenv present) + environment."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    def g(key, default):
        return os.environ.get(key, default)

    return {
        "SECRET_KEY": g("SECRET_KEY", "dev-ios-remote-secret-change-me"),
        "HOST": g("HOST", "0.0.0.0"),
        "PORT": int(g("PORT", "5001")),
        "FRONTEND_PORT": int(g("FRONTEND_PORT", "5173")),
        "OPEN_BROWSER": g("OPEN_BROWSER", "1"),
        "IOS_ENABLED": g("IOS_ENABLED", "1"),
        "IOS_COMMAND_TIMEOUT": int(g("IOS_COMMAND_TIMEOUT", "15")),
        "IOS_WDA_REMOTE_PORT": int(g("IOS_WDA_REMOTE_PORT", "8100")),
        "IOS_LOCAL_PORT_START": int(g("IOS_LOCAL_PORT_START", "18100")),
        "IOS_WDA_TIMEOUT": int(g("IOS_WDA_TIMEOUT", "15")),
        "IOS_AUTO_LAUNCH_WDA": g("IOS_AUTO_LAUNCH_WDA", "0"),
        "IOS_WDA_BUNDLE_ID": g(
            "IOS_WDA_BUNDLE_ID", "com.facebook.WebDriverAgentRunner.xctrunner"
        ),
        "IOS_WDA_TEST_RUNNER_BUNDLE_ID": g("IOS_WDA_TEST_RUNNER_BUNDLE_ID", ""),
        "IOS_WDA_XCTEST_CONFIG": g("IOS_WDA_XCTEST_CONFIG", "WebDriverAgentRunner.xctest"),
        "IOS_WDA_LAUNCH_TIMEOUT": int(g("IOS_WDA_LAUNCH_TIMEOUT", "40")),
        # go-ios engine: no-admin userspace tunnel + runwda for iOS 17+.
        "IOS_USE_GOIOS": g("IOS_USE_GOIOS", "1"),
        "IOS_GOIOS_BIN": g("IOS_GOIOS_BIN", ""),
        "IOS_GOIOS_PREFER_SYSTEM": g("IOS_GOIOS_PREFER_SYSTEM", "0"),
        "IOS_GOIOS_TUNNEL": g("IOS_GOIOS_TUNNEL", "1"),
        "IOS_GOIOS_TUNNEL_INFO_PORT": int(g("IOS_GOIOS_TUNNEL_INFO_PORT", "28100")),
        "IOS_SCREEN_PROVIDER": g("IOS_SCREEN_PROVIDER", "mjpeg"),
        "IOS_MJPEG_REMOTE_PORT": int(g("IOS_MJPEG_REMOTE_PORT", "9100")),
        "IOS_MJPEG_FRAMERATE": int(g("IOS_MJPEG_FRAMERATE", "30")),
        "IOS_MJPEG_QUALITY": int(g("IOS_MJPEG_QUALITY", "60")),
        "IOS_MJPEG_SCALING": int(g("IOS_MJPEG_SCALING", "100")),
        "IOS_SCREENSHOT_FPS": int(g("IOS_SCREENSHOT_FPS", "8")),
        "IOS_STREAM_MAX_FPS": int(g("IOS_STREAM_MAX_FPS", "30")),
        "IOS_STREAM_RECONNECT_INTERVAL": int(g("IOS_STREAM_RECONNECT_INTERVAL", "5")),
        # Idle auto-release: free a connected device (tear down WDA + forwards)
        # after this many seconds with no viewers and no control activity.
        # 0 disables. Shared device pool hygiene.
        "IOS_DEVICE_IDLE_TIMEOUT": int(g("IOS_DEVICE_IDLE_TIMEOUT", "300")),
        "IOS_ENABLE_WEBRTC": g("IOS_ENABLE_WEBRTC", "0"),
        # WebRTC software re-encode bitrate cap (bps) — a ceiling; REMB throttles
        # below it on limited bandwidth. aiortc's 3 Mbps default blurs full-retina
        # motion; ~6 Mbps is a safe sharper default. Host-side libx264 (won't crash
        # like a device encoder); CPU scales with resolution × device count.
        "IOS_WEBRTC_MAX_BITRATE": int(g("IOS_WEBRTC_MAX_BITRATE", "6000000")),
        "SOCKETIO_PING_TIMEOUT": int(g("SOCKETIO_PING_TIMEOUT", "60")),
        "SOCKETIO_PING_INTERVAL": int(g("SOCKETIO_PING_INTERVAL", "25")),
    }


def create_app():
    config = load_config()
    flask_app = Flask(__name__, static_folder=None)
    flask_app.config.update(config)
    flask_app.secret_key = config["SECRET_KEY"]  # signs the session cookie

    # Multi-user auth + reservations DB (idempotent; wipes sessions/holds on boot).
    from services.app_db import init_db

    init_db()
    # Dev CORS 鈥?the Vite dev server (5173) talks to this backend (5001).
    CORS(flask_app, resources={r"/api/*": {"origins": "*"}})

    socketio_app = SocketIO(
        flask_app,
        async_mode="threading",
        cors_allowed_origins="*",
        ping_timeout=config["SOCKETIO_PING_TIMEOUT"],
        ping_interval=config["SOCKETIO_PING_INTERVAL"],
        logger=False,
        engineio_logger=False,
    )

    # Platform adapter + API + Socket.IO handlers.
    from services import init_platform
    from api import register_blueprints
    from socketio_handlers import register_handlers

    init_platform(config)
    register_blueprints(flask_app)
    register_handlers(socketio_app)
    _install_auth_gate(flask_app)  # multi-user auth + reservation gating (always on)

    _register_spa(flask_app)
    _environment_check(config)

    return flask_app, socketio_app


_PUBLIC_API = {"/api/auth/login", "/api/auth/register", "/api/auth/check-auth", "/api/health"}


def _install_auth_gate(flask_app: Flask) -> None:
    """Central gate (only when IOS_REQUIRE_AUTH=1): login for all /api except
    public; reservation ownership for mutating /api/devices/<udid>/* control."""
    from flask import jsonify, request

    from services import auth_service, reservation_service as reservations
    from utils.app_errors import ErrorCode, err

    @flask_app.before_request
    def _gate():
        path = request.path
        if not path.startswith("/api/") or request.method == "OPTIONS":
            return None
        if path in _PUBLIC_API:
            return None
        user = auth_service.current_user()
        if user is None:
            return jsonify(err(ErrorCode.UNAUTHORIZED, "未登录或会话已过期")), 401
        # Reservation gate: mutating control on a specific device needs ownership.
        if request.method in ("POST", "PUT", "DELETE") and path.startswith("/api/devices/"):
            parts = path.split("/")
            if len(parts) >= 5:  # /api/devices/<udid>/<action...>
                try:
                    reservations.assert_owner(parts[3], user)
                except reservations.ReservationError as exc:
                    return jsonify(err(ErrorCode.RESERVATION_DENIED, str(exc))), 403
        return None


def _register_spa(flask_app: Flask) -> None:
    """Serve the built SPA (frontend/dist) with a catch-all fallback."""

    @flask_app.get("/")
    def _index():
        if (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return jsonify(
            {
                "success": True,
                "data": {"hint": "Frontend not built yet 鈥?run 'npm run build' in frontend/ "
                                  "or use the Vite dev server on :5173"},
                "message": "ok",
            }
        )

    @flask_app.get("/<path:path>")
    def _assets(path: str):
        target = DIST_DIR / path
        if target.exists() and target.is_file():
            return send_from_directory(DIST_DIR, path)
        # SPA fallback for client-side routes.
        if (DIST_DIR / "index.html").exists():
            return send_from_directory(DIST_DIR, "index.html")
        return jsonify({"success": False, "code": "NOT_FOUND", "message": path}), 404


def _environment_check(config: dict) -> None:
    import platform
    import sys

    _log.info("=" * 60)
    _log.info("WebAppFlaskauto-iOS starting")
    _log.info("Host OS        : %s", platform.system())
    _log.info("Python         : %s", sys.version.split()[0])
    _log.info("Backend port   : %s", config["PORT"])
    _log.info("Screen provider: %s", config["IOS_SCREEN_PROVIDER"])
    _log.info("SPA dist       : %s (%s)", DIST_DIR, "present" if (DIST_DIR / "index.html").exists() else "NOT BUILT")
    try:
        from services import get_adapter

        avail = get_adapter().is_backend_available()
    except (RuntimeError, AttributeError):
        avail = False
    _log.info("pymobiledevice3: %s", "available" if avail else "NOT AVAILABLE")
    if not avail:
        _log.warning("pymobiledevice3 not available 鈥?device features will error "
                     "until you 'pip install pymobiledevice3'.")
    # go-ios engine: show which binary was selected (override/system/bundled)
    # and its version, so it's obvious at a glance which one is in use.
    if str(config.get("IOS_USE_GOIOS", "1")) == "1":
        try:
            from services import get_adapter

            goios = get_adapter().goios
            if goios.is_available():
                _log.info("go-ios         : %s  (%s)", goios.version(), goios.binary())
            else:
                _log.warning("go-ios         : NOT AVAILABLE 鈥?iOS 17+ no-admin "
                             "auto-launch disabled (will fall back to pymobiledevice3).")
        except (RuntimeError, AttributeError):
            pass
    _log.info("=" * 60)


app, socketio = create_app()


def _install_process_lifecycle() -> None:
    """Reclaim orphaned go-ios agents on startup + tear everything down on exit.

    go-ios's tunnel agent is a long-lived process; a hard-kill/crash of a
    previous run can orphan it and conflict on the pinned port. We reclaim on
    start, and register atexit + SIGINT/SIGTERM so a normal stop (Ctrl+C /
    start_dev) cleanly stops the tunnel agent, runwda processes and forwards.
    """
    import atexit
    import signal
    from types import FrameType

    try:
        from services import get_adapter

        adapter = get_adapter()
    except (RuntimeError, AttributeError):
        return

    if getattr(adapter, "use_goios", False) and adapter.goios.is_available():
        try:
            adapter.tunnel.reclaim()  # clear any orphan from a prior run
        except Exception as err:  # noqa: BLE001
            _log.warning("startup tunnel reclaim skipped: %s", err)

    # Idle auto-release for the shared device pool (frees WDA/forwards when an
    # operator walks away). Only started here (not in create_app) so test
    # imports don't spawn a background reaper.
    from services.idle_reaper import IdleReaper

    reaper = IdleReaper(int(adapter.config.get("IOS_DEVICE_IDLE_TIMEOUT", 300)))
    reaper.start()

    # USB hotplug watcher: broadcast devices:changed on plug/unplug so the grid
    # auto-updates without a manual refresh.
    from services.device_watch import DeviceWatcher

    watcher = DeviceWatcher(float(adapter.config.get("IOS_DEVICE_WATCH_INTERVAL", 3.0)))
    watcher.start()

    # Reservation expiry sweeper (no-op unless reservations are used).
    with suppress(Exception):
        from services import reservation_service as reservations

        reservations.start_sweeper()

    _shutdown_done = {"v": False}

    def _cleanup() -> None:
        if _shutdown_done["v"]:
            return
        _shutdown_done["v"] = True
        _log.info("shutting down")
        with suppress(Exception):
            reaper.stop()
        with suppress(Exception):
            watcher.stop()
        try:
            adapter.shutdown()
        except Exception as cleanup_err:  # noqa: BLE001
            _log.warning("shutdown cleanup error: %s", cleanup_err)

    def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
        _cleanup()
        sys.exit(0)

    atexit.register(_cleanup)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported signal


def install_process_lifecycle() -> None:
    """Public wrapper for the process lifecycle installer."""
    _install_process_lifecycle()


if __name__ == "__main__":
    import sys

    install_process_lifecycle()
    host = app.config["HOST"]
    port = app.config["PORT"]
    _log.info("Serving on http://%s:%d", host, port)
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)


