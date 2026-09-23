"""Browser-facing HEVC access-unit framing.

Matches pymobiledevice3 ``serve-web`` /stream.bin so a WebCodecs decoder can
consume the bytes without a host-side transcode:

    [uint32 BE length][uint8 type][length-prefixed NALs]

type 0 = key, 1 = delta, 2 = key that must reset the decoder.
NAL payload is ISO/IEC 14496-15 (4-byte lengths), paired with an hvcC
``description`` delivered out of band.
"""

from __future__ import annotations


def frame_access_unit(nals: list[bytes], *, key: bool, reset: bool = False) -> bytes:
    au = b"".join(len(nal).to_bytes(4, "big") + nal for nal in nals)
    if key and reset:
        type_byte = 2
    elif key:
        type_byte = 0
    else:
        type_byte = 1
    return (len(au) + 1).to_bytes(4, "big") + bytes((type_byte,)) + au
