"""Small valid synthetic PNGs with deterministic, distinguishable pixels."""

import hashlib
import struct
import zlib


def png_bytes(seed: str = "") -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    pixel = hashlib.sha256(seed.encode()).digest()[:3]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + pixel))
        + chunk(b"IEND", b"")
    )
