"""HEVC screen transport over pymobiledevice3's own userspace RSD tunnel.

go-ios's tunnel stays in the go-ios process and cannot be shared. This
transport opens a second, in-process tunnel (one per process) and reads
CoreDevice Display RTP/HEVC. usbmux / WDA are not claimed.

Requires pymobiledevice3 >= 11.17.0. Older pins (including 9.16.0) do not
ship ``screen_stream``; callers fall back to WDA MJPEG.
"""

from __future__ import annotations

import asyncio
import struct
import threading
from contextlib import suppress
from collections.abc import Callable
from queue import Empty, Full, Queue
from typing import Optional, cast

from ios.screen_transport.base import BaseScreenTransport, EncodedPacket
from ios.screen_transport.framing import frame_access_unit
from utils.app_errors import AppError, ErrorCode
from utils.logging_setup import get_logger

_log = get_logger(__name__)

_READY_TIMEOUT = 25.0
_QUEUE_MAX = 45


def hevc_api_available() -> bool:
    """True when this interpreter can open a CoreDevice HEVC stream."""
    try:
        from pymobiledevice3.remote.core_device.screen_stream import (  # noqa: F401
            depacketize_hevc,
            hevc_codec_string_from_sps,
            hevc_decoder_configuration_record,
            open_media_receiver,
        )
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel  # noqa: F401
    except ImportError:
        return False
    return True


def _schedule(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
    """Run a zero-arg callback on ``loop``.

    typeshed types ``call_soon_threadsafe`` as ``callback, *args: Unpack[_Ts]``.
    A zero-arg callback leaves that tuple looking unfilled. The runtime call is
    still ``callback(*args)`` with no extra arguments.
    """
    schedule = cast(Callable[[Callable[[], None]], object], loop.call_soon_threadsafe)
    _ = schedule(callback)


def _wake_loop() -> None:
    return None


def build_rtcp_pli(local_ssrc: int, remote_ssrc: int) -> bytes:
    return struct.pack("!BBHII", 0x81, 0xCE, 2, local_ssrc & 0xFFFFFFFF, remote_ssrc & 0xFFFFFFFF)


def build_rtcp_rr(local_ssrc: int, remote_ssrc: int, highest_seq: int) -> bytes:
    """RR + empty SDES/CNAME. The device stalls the encoder if RR never arrives."""
    rr = struct.pack(
        "!BBHIIBBBBIIII",
        0x81,
        0xC9,
        7,
        local_ssrc & 0xFFFFFFFF,
        remote_ssrc & 0xFFFFFFFF,
        0,
        0,
        0,
        0,
        highest_seq & 0xFFFFFFFF,
        0,
        0,
        0,
    )
    sdes = struct.pack(
        "!BBHIBBBB",
        0x81,
        0xCA,
        2,
        local_ssrc & 0xFFFFFFFF,
        0x01,
        0x00,
        0x00,
        0x00,
    )
    return rr + sdes


class HevcRsdTransport(BaseScreenTransport):
    name = "hevc"

    def __init__(self, udid: str):
        self.udid = udid
        self._queue: Queue[EncodedPacket] = Queue(maxsize=_QUEUE_MAX)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._key_event: Optional[asyncio.Event] = None
        self._error: Optional[BaseException] = None
        self._healthy = False
        self._codec_string: Optional[str] = None
        self._description: Optional[bytes] = None
        self._want_key = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not hevc_api_available():
            raise AppError(
                ErrorCode.SCREEN_PROVIDER_FAILED,
                "This pymobiledevice3 build has no CoreDevice HEVC API (need >= 11.17.0)",
            )
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._healthy = False
        self._thread = threading.Thread(
            target=self._thread_main, name=f"hevc-{self.udid[:8]}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(_READY_TIMEOUT):
            self.stop()
            raise AppError(
                ErrorCode.SCREEN_PROVIDER_FAILED,
                "HEVC stream did not produce a keyframe in time",
            )
        if self._error is not None:
            err = self._error
            self.stop()
            raise AppError(
                ErrorCode.SCREEN_PROVIDER_FAILED,
                f"HEVC stream failed: {err}",
            ) from err

    def stop(self) -> None:
        self._stop.set()
        self._healthy = False
        loop = self._loop
        if loop is not None and loop.is_running():
            _schedule(loop, _wake_loop)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._thread = None

    def read_packet(self) -> Optional[EncodedPacket]:
        if self._stop.is_set() and self._queue.empty():
            return None
        try:
            return self._queue.get(timeout=0.5)
        except Empty:
            return None

    def health(self) -> bool:
        alive = self._thread is not None and self._thread.is_alive()
        return bool(self._healthy and alive and not self._stop.is_set())

    def request_keyframe(self) -> None:
        loop = self._loop
        ev = self._key_event
        if loop is None or ev is None or not loop.is_running():
            return

        def _ask_keyframe() -> None:
            ev.set()

        _schedule(loop, _ask_keyframe)

    def _fail(self, exc: BaseException) -> None:
        self._error = exc
        self._healthy = False
        self._ready.set()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 — surfaced via start()/health
            _log.warning("hevc transport %s ended: %s", self.udid[:8], exc)
            self._fail(exc)
        finally:
            self._healthy = False
            self._ready.set()

    async def _run(self) -> None:
        from pymobiledevice3.remote.core_device.display_service import DisplayService
        from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        self._loop = asyncio.get_running_loop()
        self._key_event = asyncio.Event()
        # Callers already gate on iOS 27. Refuse the pre-17.4 RemotePairing
        # fallback so a mis-detected version cannot open that path either.
        tunnel = UserspaceRsdTunnel(serial=self.udid, remotepairing_fallback=False)
        rsd = await tunnel.aopen()
        try:
            if self._stop.is_set():
                return
            await self._stream(rsd, DisplayService, open_media_receiver)
        finally:
            with suppress(Exception):
                await tunnel.aclose()

    async def _stream(self, rsd, display_cls, open_media_receiver) -> None:
        sender_ip = rsd.service.address[0]
        async with display_cls(rsd) as service:
            transport, receiver_ip = open_media_receiver(
                service, (8 * 1024 * 1024, 4 * 1024 * 1024)
            )
            try:
                answer = await service.start_video_stream(
                    receiver_ip=receiver_ip,
                    receiver_port=transport.port,
                    sender_ip=sender_ip,
                    display_id=1,
                )
                cfg = (answer.get("connection") or {}).get("streamConfig") or {}
                source_port = int(cfg.get("SourcePort") or 0)
                local_ssrc = int(cfg.get("RemoteSSRC") or 0)
                remote_ssrc = int(cfg.get("LocalSSRC") or 0)
                rtcp_dest = (sender_ip, source_port) if source_port else None
                self._healthy = True
                rtcp_task = None
                if rtcp_dest and local_ssrc and remote_ssrc:
                    rtcp_task = asyncio.create_task(
                        self._rtcp_loop(transport, rtcp_dest, local_ssrc, remote_ssrc)
                    )
                try:
                    await self._recv_loop(transport, rtcp_dest, local_ssrc, remote_ssrc)
                finally:
                    if rtcp_task is not None:
                        rtcp_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await rtcp_task
            finally:
                with suppress(Exception):
                    await display_cls.stop_all_streams(rsd)
                with suppress(Exception):
                    transport.close()

    async def _rtcp_loop(self, transport, dest, local_ssrc: int, remote_ssrc: int) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            if self._stop.is_set():
                return
            seq = getattr(self, "_highest_seq", 0)
            if not seq:
                continue
            with suppress(Exception):
                await transport.sendto(build_rtcp_rr(local_ssrc, remote_ssrc, seq), *dest)

    async def _recv_loop(self, transport, rtcp_dest, local_ssrc: int, remote_ssrc: int) -> None:
        from pymobiledevice3.remote.core_device.screen_stream import (
            depacketize_hevc,
            hevc_codec_string_from_sps,
            hevc_decoder_configuration_record,
        )

        fu = bytearray()
        current: list[bytes] = []
        au_key = False
        au_corrupt = False
        reset_next_key = False
        last_seq: Optional[int] = None
        vps = sps = pps = None
        self._highest_seq = 0
        while not self._stop.is_set():
            if self._want_key or (self._key_event is not None and self._key_event.is_set()):
                self._want_key = False
                if self._key_event is not None:
                    self._key_event.clear()
                reset_next_key = True
                await self._send_pli(transport, rtcp_dest, local_ssrc, remote_ssrc)
            try:
                data = await asyncio.wait_for(transport.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
                return
            if len(data) < 12:
                continue
            pt = data[1] & 0x7F
            if 64 <= pt <= 95:
                continue
            marker = (data[1] >> 7) & 1
            cc = data[0] & 0x0F
            header_len = 12 + cc * 4
            if data[0] & 0x10 and header_len + 4 <= len(data):
                ext_len = int.from_bytes(data[header_len + 2 : header_len + 4], "big")
                header_len += 4 + ext_len * 4
            if header_len > len(data):
                continue
            payload = data[header_len:]
            seq = int.from_bytes(data[2:4], "big")
            self._highest_seq = seq
            if last_seq is not None and seq != ((last_seq + 1) & 0xFFFF):
                forward = ((seq - last_seq) & 0xFFFF) < 0x8000
                if forward:
                    fu.clear()
                    au_corrupt = True
                    reset_next_key = True
                    await self._send_pli(transport, rtcp_dest, local_ssrc, remote_ssrc)
            if last_seq is None or ((seq - last_seq) & 0xFFFF) < 0x8000:
                last_seq = seq
            nals: list[bytes] = []
            depacketize_hevc(payload, fu, nals)
            for nal in nals:
                if not nal:
                    continue
                nt = (nal[0] >> 1) & 0x3F
                if nt == 32:
                    vps = nal
                elif nt == 33:
                    sps = nal
                    if self._codec_string is None:
                        with suppress(Exception):
                            self._codec_string = hevc_codec_string_from_sps(nal)
                elif nt == 34:
                    pps = nal
                if nt in (19, 20, 21):
                    au_key = True
                current.append(nal)
            if not marker:
                continue
            if au_corrupt or not current:
                current = []
                au_key = False
                au_corrupt = False
                continue
            if vps and sps and pps and self._description is None and self._codec_string:
                with suppress(Exception):
                    self._description = hevc_decoder_configuration_record(vps, sps, pps)
            reset = bool(au_key and reset_next_key)
            if au_key and reset:
                reset_next_key = False
            packet = EncodedPacket(
                codec="hevc",
                keyframe=au_key,
                reset=reset,
                data=frame_access_unit(current, key=au_key, reset=reset),
                codec_string=self._codec_string,
                description=self._description,
            )
            current = []
            au_key = False
            self._enqueue(packet)
            if packet.keyframe and packet.description and packet.codec_string:
                self._ready.set()

    def _arm_resync(self) -> None:
        """A dropped delta leaves a gap. Ask the phone for a key and mark it reset."""
        self._want_key = True
        if self._key_event is not None:
            self._key_event.set()

    def _enqueue(self, packet: EncodedPacket) -> None:
        # A full queue means the browser fell behind. This phone may not emit
        # another key for seconds, so a dropped delta must request one. The
        # next key is framed as a decoder reset.
        if self._queue.full():
            if not packet.keyframe:
                self._arm_resync()
                return
            with suppress(Empty):
                self._queue.get_nowait()
            packet = _mark_reset(packet)
        try:
            self._queue.put_nowait(packet)
        except Full:
            self._arm_resync()

    @staticmethod
    async def _send_pli(transport, dest, local_ssrc: int, remote_ssrc: int) -> None:
        if not dest or not local_ssrc or not remote_ssrc:
            return
        with suppress(Exception):
            await transport.sendto(build_rtcp_pli(local_ssrc, remote_ssrc), *dest)


def _mark_reset(packet: EncodedPacket) -> EncodedPacket:
    if not packet.keyframe or packet.reset or len(packet.data) < 5:
        return packet
    data = bytearray(packet.data)
    data[4] = 2
    packet.data = bytes(data)
    packet.reset = True
    return packet
