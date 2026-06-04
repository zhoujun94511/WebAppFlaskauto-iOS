"""WebRTC transport: stream a device's screen to the browser over a
PeerConnection instead of pushing JPEG frames through Socket.IO.

Pipeline:  ScreenProvider (WDA MJPEG → JPEG bytes)
             → JpegVideoTrack (decode JPEG → av.VideoFrame)
             → aiortc RTCPeerConnection (VP8/H264) → browser <video>.

aiortc is asyncio; Flask-SocketIO runs in ``threading`` mode, so we own a
dedicated asyncio loop on a daemon thread and marshal calls onto it with
``run_coroutine_threadsafe``. Signaling is non-trickle (aiortc finishes ICE
gathering during setLocalDescription, so host candidates are baked into the
answer SDP) — one offer/answer round-trip over Socket.IO, no separate ICE
messages, which is all that's needed on localhost/LAN.
"""

from __future__ import annotations

import asyncio
import fractions
import json
import threading
import time
from contextlib import suppress
from typing import Any, Dict, Optional, Tuple

import av
from av.error import FFmpegError
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

from ios.screen_provider.base_provider import BaseScreenProvider
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)
_VIDEO_CLOCK = 90000  # 90 kHz RTP video clock


def _even_dims(frame: "av.VideoFrame") -> "av.VideoFrame":
    """Force a frame to even width AND height before it reaches the encoder.

    libx264 with yuv420p (4:2:0 chroma subsampling) REQUIRES both dimensions to
    be even — an odd width/height makes avcodec_open2(libx264) fail with a
    generic 'external library' error, which permanently kills aiortc's RTP
    sender task (the PC reports 'connected' but never sends a frame → black
    screen, while DataChannel control still works). Several iPhones report odd
    pixel widths (e.g. 1179×2556 on the 14/15 Pro), so the MJPEG-decoded frame
    can be odd. Reformatting to the nearest even size scales by at most 1px,
    which is imperceptible, and keeps the source pixel format. Verified: 1179
    and 1125 wide both fail to open raw and succeed after this normalization."""
    w, h = frame.width, frame.height
    ew, eh = w - (w & 1), h - (h & 1)
    if (ew, eh) == (w, h) or ew <= 0 or eh <= 0:
        return frame
    fixed = frame.reformat(width=ew, height=eh)
    # Preserve timing if the source frame carries it (recv() re-stamps pts/
    # time_base right after, so these may legitimately be None here).
    fixed.pts = frame.pts
    if frame.time_base is not None:
        fixed.time_base = frame.time_base
    return fixed


def _quiet_loop_exceptions(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Demote aiortc/aioice teardown noise from ERROR tracebacks to debug.

    When a PeerConnection is closed mid-negotiation (a fast device/view switch),
    the ICE transport is already gone, so late STUN retries and the trailing
    __connect() task raise into the loop:
      • InvalidStateError('RTCIceTransport is closed')
      • AttributeError: 'NoneType' object has no attribute 'sendto'
        (aioice Transaction.__retry firing after the transport was torn down)
    These are harmless — the PC was intentionally killed — but asyncio prints
    them as alarming multi-line tracebacks. Swallow exactly these, and defer
    everything else to asyncio's normal handler so real errors still surface."""
    exc = context.get("exception")
    text = f"{type(exc).__name__ if exc else ''}: {exc} | {context.get('message', '')}"
    benign = (
        "RTCIceTransport is closed" in text
        or "Transaction.__retry" in text
        or ("sendto" in text and "NoneType" in text)
        or ("call_exception_handler" in text and "NoneType" in text)
    )
    if benign:
        _log.debug("webrtc teardown noise suppressed: %s", text.strip(" |"))
        return
    loop.default_exception_handler(context)


class JpegVideoTrack(MediaStreamTrack):
    """A live video track fed by a JPEG ScreenProvider, with mid-stream
    self-heal: if the provider dies (e.g. WDA's MJPEG socket drops) we swap to a
    fresh provider (screenshot) WITHOUT tearing down the PeerConnection, so the
    browser <video> keeps playing. Bounded by ``max_recovery``.

    ``provider_factory(recover: bool)`` returns an UNSTARTED provider:
    recover=False → preferred (MJPEG); recover=True → robust fallback
    (screenshot). The track owns start()/stop()."""

    kind = "video"

    def __init__(self, provider_factory, max_recovery: int = 3,
                 initial: Optional[BaseScreenProvider] = None):
        super().__init__()
        self._factory = provider_factory
        self._max_recovery = max_recovery
        self._recoveries = 0
        self._provider: Optional[BaseScreenProvider] = initial
        self._decoder: Any = av.codec.CodecContext.create("mjpeg", "r")
        self._t0: Optional[float] = None
        self._last: Optional[av.VideoFrame] = None
        self._misses = 0

    def open_initial(self) -> None:
        """Open the first provider (MJPEG → screenshot). Raises if both fail —
        caller turns that into webrtc:error before any PC is created. A no-op
        when an already-started provider was injected via ``initial``."""
        if self._provider is not None:
            return
        try:
            self._open(recover=False)
        except AppError:  # MJPEG open failed; try screenshot
            self._open(recover=True)

    def _open(self, recover: bool) -> None:
        if self._provider is not None:
            with suppress(Exception):
                self._provider.stop()
        provider = self._factory(recover)
        provider.start()  # opens MJPEG socket / warms screenshot; may raise
        self._provider = provider
        self._decoder = av.codec.CodecContext.create("mjpeg", "r")
        self._misses = 0

    async def recv(self) -> av.VideoFrame:
        # Produce the next frame to encode. We loop until we actually have one,
        # rather than handing aiortc a degenerate placeholder: aiortc opens
        # libx264 lazily on the first frame, so a bogus first frame would open
        # the encoder at the wrong size. Before the first real frame we keep
        # waiting; after it, we reuse the last good frame as filler when the
        # provider briefly starves. The returned frame is normalized to even
        # dimensions (see _even_dims) — odd width/height breaks libx264 open.
        frame: Optional[av.VideoFrame] = None
        while frame is None:
            # read_frame() blocks until the next JPEG — run it off the event loop
            # so we don't stall other peers. This also paces us to device fps.
            jpeg = await asyncio.to_thread(self._provider.read_frame)
            if jpeg:
                self._misses = 0
                try:
                    decoded = self._decoder.decode(av.packet.Packet(jpeg))
                    if decoded:
                        frame = decoded[0]
                except FFmpegError:
                    pass
            elif self._misses < 30 and self._provider.health():
                self._misses += 1
                await asyncio.sleep(0.02)
            elif self._recoveries < self._max_recovery:
                # Provider died mid-stream — swap to the screenshot fallback and
                # keep the PeerConnection alive (no renegotiation, no drop).
                self._recoveries += 1
                _log.info("webrtc track self-heal: swapping to screenshot provider (%d/%d)",
                          self._recoveries, self._max_recovery)
                try:
                    await asyncio.to_thread(self._open, True)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("webrtc track recover failed: %s", exc)
                    await asyncio.sleep(0.5)
            else:
                raise MediaStreamError("screen provider permanently unavailable")
            # Once we've emitted at least one real frame, a momentary miss reuses
            # the last good frame (keeps fps up, correct size). Before that, the
            # loop keeps polling — we'd rather make aiortc wait than open the
            # encoder on a bogus size.
            if frame is None and self._last is not None:
                frame = self._last
        # Normalize to even dimensions BEFORE the encoder ever sees the frame.
        # libx264/yuv420p rejects odd width/height (several iPhones report odd
        # pixel widths, e.g. 1179) — without this the very first encode fails at
        # avcodec_open2 and the video track dies (connected but black).
        frame = _even_dims(frame)
        if self._t0 is None:
            self._t0 = time.monotonic()
        frame.pts = int((time.monotonic() - self._t0) * _VIDEO_CLOCK)
        frame.time_base = fractions.Fraction(1, _VIDEO_CLOCK)
        self._last = frame
        return frame

    def stop(self) -> None:  # noqa: D401
        super().stop()
        with suppress(Exception):
            if self._provider is not None:
                self._provider.stop()


class WebRTCBridge:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.enabled = str(self.config.get("IOS_ENABLE_WEBRTC", "0")) == "1"
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # (sid, udid) -> (RTCPeerConnection, JpegVideoTrack)
        self._peers: Dict[Tuple[str, str], Tuple[RTCPeerConnection, MediaStreamTrack]] = {}
        # (sid, udid) -> monotonic offer generation. A fast device switch in
        # single view remounts the stage and fires a fresh offer for the SAME
        # (sid, udid) while the previous one is still mid-setup (the ~0.5s MJPEG
        # build). Without a guard, the newer offer's _close_peer() tears down the
        # older offer's PC, which then raises "RTCPeerConnection is closed" at
        # createAnswer and leaves a half-dead connection (front-end shows running
        # but no frames arrive → black screen, control still works over HTTP).
        # The generation lets a superseded offer bail cleanly after every await.
        self._gen: Dict[Tuple[str, str], int] = {}
        self._tune_encoder_bitrate()

    def _tune_encoder_bitrate(self) -> None:
        """Raise aiortc's software-encoder bitrate caps. Defaults (H264 1→3 Mbps,
        VP8 0.5→1.5) are far too low for full-retina motion, so a fast swipe gets
        crushed into blur. Bump the module-level caps (read at encode time) so the
        encoder can spend bits on motion. Tunable via IOS_WEBRTC_MAX_BITRATE."""
        try:
            from aiortc.codecs import h264 as _h, vpx as _v
            maxb = int(self.config.get("IOS_WEBRTC_MAX_BITRATE", 8_000_000))
            startb = max(2_000_000, maxb // 2)
            for m in (_h, _v):
                m.MAX_BITRATE = maxb
                m.DEFAULT_BITRATE = min(startb, maxb)
                m.MIN_BITRATE = min(getattr(m, "MIN_BITRATE", 1_000_000), 1_000_000)
            _log.info("webrtc encoder bitrate cap raised to %d bps (start %d)", maxb, startb)
        except Exception as exc:  # noqa: BLE001 — never block startup on a tuning knob
            _log.warning("could not tune webrtc encoder bitrate: %s", exc)

    # ── event loop plumbing ──────────────────────────────────────────
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            self._loop = asyncio.new_event_loop()
            self._loop.set_exception_handler(_quiet_loop_exceptions)
            self._thread = threading.Thread(
                target=self._loop.run_forever, name="webrtc-loop", daemon=True
            )
            self._thread.start()
            _log.info("WebRTC asyncio loop started")
            return self._loop

    def _run(self, coro, timeout: float = 30.0):
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # ── public API (called from Socket.IO threading handlers) ────────
    def handle_offer(self, sid: str, udid: str, provider_factory,
                     sdp: str, sdp_type: str) -> Optional[dict]:
        """Create a PeerConnection for (sid, udid), answer the offer, and return
        the answer SDP (with ICE candidates already gathered). ``provider_factory``
        builds a JPEG provider on demand (and again on mid-stream self-heal).

        Returns None when this offer was SUPERSEDED by a newer offer for the same
        (sid, udid) before it finished — the caller must NOT emit an answer or an
        error in that case (it's expected churn, not a failure)."""
        return self._run(self._handle_offer(sid, udid, provider_factory, sdp, sdp_type))

    @staticmethod
    def _build_track(provider_factory):
        """Open the first provider (MJPEG → screenshot fallback) and wrap it in a
        JpegVideoTrack. Runs in a worker thread; raising here becomes
        webrtc:error before any PC exists."""
        try:
            provider = provider_factory(False)
            provider.start()
        except AppError:
            provider = provider_factory(True)  # robust fallback (screenshot, JPEG)
            provider.start()
        return JpegVideoTrack(provider_factory, initial=provider)

    async def _handle_offer(self, sid, udid, provider_factory, sdp, sdp_type) -> Optional[dict]:
        key = (sid, udid)
        gen = self._gen.get(key, 0) + 1
        self._gen[key] = gen
        superseded = lambda: self._gen.get(key) != gen  # noqa: E731

        await self._close_peer(sid, udid)  # replace any prior PC for this pair
        if superseded():
            return None
        # Build the JPEG provider (MJPEG forward + first frame, ~0.5s) BEFORE
        # creating the PC. If a newer offer for this pair landed meanwhile, drop
        # this one now — never create a PC a newer offer will just tear down.
        track = await asyncio.to_thread(self._build_track, provider_factory)  # raises → webrtc:error, no PC
        if superseded():
            with suppress(Exception):
                track.stop()
            return None
        pc = RTCPeerConnection()
        pc.addTrack(track)
        self._peers[key] = (pc, track)
        self._prefer_h264(pc, track)  # H264-first; browser falls back to VP8 if needed

        @pc.on("connectionstatechange")
        async def _on_state():
            _log.info("webrtc %s/%s state=%s", sid[:6], udid[:8], pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                # Only evict if THIS pc is still the registered one — an old pc's
                # late "closed" event must not drop a newer pc for the same pair.
                registered = self._peers.get(key)
                if registered and registered[0] is pc:
                    await self._close_peer(sid, udid)

        @pc.on("datachannel")
        def _on_datachannel(channel):
            # Browser opens a "control" channel and streams tap/swipe/button/key
            # over it (no HTTP per gesture). WDA is still the latency wall, but
            # the transport is unified with the video PeerConnection.
            self._setup_control_channel(channel, udid)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)  # blocks until ICE gathering done
        except InvalidStateError:
            # The PC was closed mid-setup by a newer offer's _close_peer — bail.
            if superseded():
                return None
            raise
        # Last-moment supersede check: only emit if we're still the live PC.
        cur = self._peers.get(key)
        if superseded() or not cur or cur[0] is not pc:
            return None
        ld = pc.localDescription
        return {"sdp": ld.sdp, "type": ld.type}

    @staticmethod
    def _prefer_h264(pc, track) -> None:
        """Offer H264 first (cheaper software encode, better at low bitrate).
        No knob: if the browser lacks H264 it just negotiates VP8 — automatic
        fallback. Bitrate adapts on its own from the browser's REMB."""
        from aiortc import RTCRtpSender

        caps = RTCRtpSender.getCapabilities("video").codecs
        h264 = [c for c in caps if c.mimeType.lower() == "video/h264"]
        if not h264:
            return
        rest = [c for c in caps if c.mimeType.lower() != "video/h264"]
        tr = next((t for t in pc.getTransceivers()
                   if getattr(t.sender, "track", None) is track), None)
        if tr is not None:
            with suppress(Exception):
                tr.setCodecPreferences(h264 + rest)

    # ── control data channel ─────────────────────────────────────────
    def _setup_control_channel(self, channel, udid: str) -> None:
        busy = {"touch": False}  # server-side single-flight for tap/swipe

        @channel.on("message")
        def _on_message(message):
            asyncio.ensure_future(self._handle_control(channel, udid, message, busy))

    async def _handle_control(self, channel, udid: str, message, busy: dict) -> None:
        try:
            msg = json.loads(message) if isinstance(message, str) else {}
        except (ValueError, TypeError):
            return
        kind = msg.get("type")
        # Drop a new gesture while one is in flight — freshest input wins, and
        # WDA serializes anyway (mirrors the HTTP path's single-flight).
        if kind in ("tap", "swipe", "longpress", "doubletap"):
            if busy["touch"]:
                return
            busy["touch"] = True
        try:
            await asyncio.to_thread(self._dispatch_control, udid, msg)
        except AppError as exc:
            with suppress(Exception):
                channel.send(json.dumps({"ok": False, "code": exc.code, "message": exc.message}))
        except Exception as exc:  # noqa: BLE001
            _log.warning("webrtc control %s failed: %s", kind, exc)
            with suppress(Exception):
                channel.send(json.dumps({"ok": False, "message": str(exc)}))
        finally:
            if kind in ("tap", "swipe", "longpress", "doubletap"):
                busy["touch"] = False

    @staticmethod
    def _dispatch_control(udid: str, msg: dict) -> None:
        from services.ios_control_service import IOSControlService
        from services.runtime_state import state

        state.touch(udid)
        svc = IOSControlService()
        kind = msg.get("type")
        if kind == "tap":
            svc.tap(udid, msg)
        elif kind == "swipe":
            svc.swipe(udid, msg)
        elif kind == "longpress":
            svc.long_press(udid, msg)
        elif kind == "doubletap":
            svc.double_tap(udid, msg)
        elif kind == "button":
            svc.button(udid, str(msg.get("name", "")))
        elif kind == "key":
            svc.key(udid, str(msg.get("name", "")))
        elif kind == "text":
            svc.input_text(udid, {"text": str(msg.get("text", ""))})
        else:
            raise AppError(ErrorCode.BAD_REQUEST, f"unknown control type '{kind}'")

    def stop(self, sid: str, udid: str) -> None:
        with suppress(Exception):
            self._run(self._close_peer(sid, udid), timeout=10)

    def stop_device(self, udid: str) -> None:
        """Close all peer connections for a device (e.g. on reservation release)."""
        for s, u in [k for k in list(self._peers.keys()) if k[1] == udid]:
            self.stop(s, u)

    def stop_sid(self, sid: str) -> None:
        """Close all peer connections for a disconnected client."""
        pairs = [k for k in list(self._peers.keys()) if k[0] == sid]
        for _, udid in pairs:
            self.stop(sid, udid)

    async def _close_peer(self, sid, udid) -> None:
        entry = self._peers.pop((sid, udid), None)
        if not entry:
            return
        pc, track = entry
        with suppress(Exception):
            track.stop()
        with suppress(Exception):
            await pc.close()

    def shutdown(self) -> None:
        with suppress(Exception):
            self._run(self._close_all(), timeout=10)

    async def _close_all(self) -> None:
        for key in list(self._peers.keys()):
            await self._close_peer(*key)
