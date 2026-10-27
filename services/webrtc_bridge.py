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
# ICE gathering sits between opening MJPEG and the first recv(). The phone
# closes an unread stream in a few seconds, so the pump must already be
# draining it. Reopen that stream before giving up on screenshots.
_MJPEG_REOPENS = 3
_SCREENSHOT_RETRY_S = 5.0


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
    """A live video track fed by a JPEG ScreenProvider.

    A background pump starts reading as soon as the provider is open. WebRTC
    ICE gathering happens after that and can take several seconds; an unread
    WDA MJPEG socket is closed by the phone, and the track used to spend the
    rest of the session on screenshot polling. The pump keeps only the newest
    JPEG so that wait does not kill the stream. ``recv`` encodes that JPEG.

    If the source still ends, MJPEG is reopened before any screenshot fallback.
    Screenshot mode keeps trying to return to MJPEG. ``provider_factory(recover)``
    returns an UNSTARTED provider: False is MJPEG, True is screenshot.
    """

    kind = "video"

    def __init__(self, provider_factory, max_recovery: int = 3,
                 initial: Optional[BaseScreenProvider] = None):
        super().__init__()
        self._factory = provider_factory
        self._max_recovery = max_recovery
        self._recoveries = 0
        self._mjpeg_reopens = 0
        self._using_screenshot = False
        self._screenshot_since = 0.0
        self._provider: Optional[BaseScreenProvider] = initial
        self._decoder: Any = av.codec.CodecContext.create("mjpeg", "r")
        self._t0: Optional[float] = None
        self._last: Optional[av.VideoFrame] = None
        self._state_lock = threading.Lock()
        self._latest: Optional[bytes] = None
        self._seq = 0
        self._sent_seq = 0
        self._pump_stop = threading.Event()
        self._pump: Optional[threading.Thread] = None
        self._source_dead = threading.Event()
        self._pump_misses = 0
        if self._provider is not None:
            self._start_pump()

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
            self._using_screenshot = True
            self._screenshot_since = time.monotonic()

    def _open(self, recover: bool) -> None:
        self._stop_pump()
        old = self._provider
        self._provider = None
        if old is not None:
            with suppress(Exception):
                old.stop()
        provider = self._factory(recover)
        provider.start()  # opens MJPEG socket / warms screenshot; may raise
        self._provider = provider
        self._decoder = av.codec.CodecContext.create("mjpeg", "r")
        self._pump_misses = 0
        self._source_dead.clear()
        with self._state_lock:
            self._latest = None
        self._start_pump()

    def _start_pump(self) -> None:
        self._pump_stop.clear()
        self._pump = threading.Thread(
            target=self._pump_loop, name="webrtc-jpeg-pump", daemon=True,
        )
        self._pump.start()

    def _stop_pump(self) -> None:
        self._pump_stop.set()
        thread = self._pump
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=2)
        self._pump = None
        self._pump_stop.clear()

    def _pump_loop(self) -> None:
        """Drain the provider so the socket stays alive before ``recv`` runs."""
        while not self._pump_stop.is_set():
            provider = self._provider
            if provider is None:
                break
            jpeg = provider.read_frame()
            if self._pump_stop.is_set():
                break
            if jpeg:
                self._pump_misses = 0
                with self._state_lock:
                    self._latest = jpeg
                    self._seq += 1
                continue
            if provider.health() and self._pump_misses < 30:
                self._pump_misses += 1
                time.sleep(0.02)
                continue
            _log.warning(
                "webrtc source stopped after %d frame(s): provider=%s healthy=%s",
                self._seq, getattr(provider, "name", "?"), provider.health(),
            )
            self._source_dead.set()
            break

    def _copy_latest(self) -> tuple[Optional[bytes], int]:
        with self._state_lock:
            return self._latest, self._seq

    def _decode(self, jpeg: bytes) -> Optional[av.VideoFrame]:
        try:
            decoded = self._decoder.decode(av.packet.Packet(jpeg))
        except FFmpegError:
            return None
        return decoded[0] if decoded else None

    def _heal_sync(self) -> None:
        name = getattr(self._provider, "name", "?")
        frames = self._seq
        self._source_dead.clear()
        if name != "screenshot" and self._mjpeg_reopens < _MJPEG_REOPENS:
            self._mjpeg_reopens += 1
            _log.info(
                "webrtc track reopening MJPEG (%d/%d) after %d frame(s), previous=%s",
                self._mjpeg_reopens, _MJPEG_REOPENS, frames, name,
            )
            try:
                self._open(False)
                return
            except AppError as exc:
                _log.warning(
                    "webrtc MJPEG reopen failed after %d frame(s): %s", frames, exc.code,
                )
        self._recoveries += 1
        if self._recoveries > self._max_recovery:
            raise MediaStreamError("screen provider permanently unavailable")
        _log.info(
            "webrtc track self-heal: swapping to screenshot provider (%d/%d) after %d frame(s)",
            self._recoveries, self._max_recovery, frames,
        )
        self._open(True)
        self._using_screenshot = True
        self._screenshot_since = time.monotonic()

    def _attempt_mjpeg_return(self) -> None:
        """Screenshot mode is the last resort. Try the MJPEG socket again."""
        self._screenshot_since = time.monotonic()
        try:
            provider = self._factory(False)
            provider.start()
        except AppError as exc:
            _log.info("webrtc MJPEG not ready yet, staying on screenshot: %s", exc.code)
            return
        self._stop_pump()
        old = self._provider
        self._provider = provider
        self._using_screenshot = False
        self._mjpeg_reopens = 0
        self._source_dead.clear()
        self._decoder = av.codec.CodecContext.create("mjpeg", "r")
        with self._state_lock:
            self._latest = None
        self._start_pump()
        if old is not None:
            with suppress(Exception):
                old.stop()
        _log.info("webrtc track returned to MJPEG")

    async def recv(self) -> av.VideoFrame:
        # Produce the next frame to encode. The pump, not this call, reads the
        # provider — ICE gathering must not leave the MJPEG socket idle.
        # libx264 opens on the first real frame, so wait for one instead of
        # inventing a placeholder. After that, a brief gap repeats the last frame.
        frame: Optional[av.VideoFrame] = None
        waited = 0.0
        while frame is None:
            if (
                self._using_screenshot
                and time.monotonic() - self._screenshot_since >= _SCREENSHOT_RETRY_S
            ):
                await asyncio.to_thread(self._attempt_mjpeg_return)
                self._sent_seq = 0
            jpeg, seq = self._copy_latest()
            if jpeg is not None and seq != self._sent_seq:
                self._sent_seq = seq
                frame = self._decode(jpeg)
                waited = 0.0
                if frame is None:
                    continue
            elif self._source_dead.is_set():
                try:
                    await asyncio.to_thread(self._heal_sync)
                except MediaStreamError:
                    raise
                except Exception as exc:  # noqa: BLE001 — keep the PC, try again
                    _log.warning("webrtc track recover failed: %s", exc)
                    self._source_dead.set()
                    await asyncio.sleep(0.5)
                self._sent_seq = 0
                waited = 0.0
            elif self._last is not None and waited >= 0.2:
                frame = self._last
            else:
                await asyncio.sleep(0.02)
                waited += 0.02
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
        self._stop_pump()
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
