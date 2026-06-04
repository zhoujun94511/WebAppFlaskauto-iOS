"""WebRTC signaling Socket.IO events: offer/answer (non-trickle) + stop.

The browser creates an offer, waits for its ICE gathering to finish, and sends
``webrtc:offer``. We bring up WDA if needed, pick a JPEG ScreenProvider (MJPEG,
falling back to screenshot), hand it to the WebRTCBridge, and return the answer
(with server candidates already in the SDP) as ``webrtc:answer``.
"""

from __future__ import annotations

from typing import cast

from flask import request

from services import get_adapter
from services.runtime_state import state
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)


def _sid() -> str:
    return cast(str, getattr(request, "sid"))


def _provider_factory(udid: str):
    """Return a factory the WebRTC track calls to (re)build a JPEG provider — on
    first open and again on each mid-stream self-heal. Ensures WDA is up, then
    MJPEG (recover=False) or the robust screenshot fallback (recover=True).
    Runs in the bridge's worker thread; blocking calls are fine there."""

    def factory(recover: bool):
        adapter = get_adapter()
        if not adapter.check_wda(udid):
            adapter.connect(udid)  # re-establish WDA (may raise AppError)
        if recover:
            return adapter.fallback_provider(udid)  # screenshot (JPEG)
        try:
            return adapter.make_screen_provider(udid, "mjpeg")
        except AppError:
            return adapter.fallback_provider(udid)

    return factory


def register(socketio) -> None:
    @socketio.on("webrtc:offer")
    def _offer(data):
        data = data or {}
        udid, sdp, sdp_type = data.get("udid"), data.get("sdp"), data.get("type")
        sid = _sid()
        if not (udid and sdp and sdp_type):
            socketio.emit("webrtc:error",
                          {"code": "BAD_REQUEST", "message": "udid/sdp/type required"},
                          room=sid)
            return
        from socketio_handlers import reservation_gate

        denied = reservation_gate(udid)
        if denied:
            socketio.emit("webrtc:error", {"code": "RESERVATION_DENIED", "message": denied}, room=sid)
            return
        try:
            state.add_viewer(udid, sid)
            state.touch(udid)
            answer = get_adapter().webrtc.handle_offer(
                sid, udid, _provider_factory(udid), sdp, sdp_type)
            # None = this offer was superseded by a newer one for the same
            # (sid, udid) (fast device switch). Expected churn — the newer offer
            # owns the connection now, so don't emit a stale answer or an error.
            if answer:
                socketio.emit("webrtc:answer", {"udid": udid, **answer}, room=sid)
        except AppError as exc:
            state.remove_viewer(udid, sid)
            socketio.emit("webrtc:error", {**exc.to_dict(), "udid": udid}, room=sid)
        except Exception as exc:  # noqa: BLE001 — never crash the worker thread
            state.remove_viewer(udid, sid)
            _log.exception("webrtc:offer failed for %s", udid)
            socketio.emit("webrtc:error",
                          {"code": ErrorCode.INTERNAL_ERROR, "message": str(exc), "udid": udid},
                          room=sid)

    @socketio.on("webrtc:stop")
    def _stop(data):
        udid = (data or {}).get("udid")
        if not udid:
            return
        sid = _sid()
        state.remove_viewer(udid, sid)
        get_adapter().webrtc.stop(sid, udid)
