"""Conservative image signature/container checks, without decoding pixels."""

from __future__ import annotations

import struct
import zlib
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".tif",
    ".tiff",
    ".ico",
    ".avif",
    ".heif",
    ".heic",
}


class InvalidImagePayload(ValueError):
    """The response does not contain a recognized image container."""


def validate_image_payload(
    content: bytes,
    *,
    name: str,
    content_type: str | None,
    declared_content_type: str | None = None,
) -> None:
    """Validate expected images; do not expose their content or URL in diagnostics."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        try:
            suffix = PurePosixPath(unquote(urlsplit(name).path)).suffix.lower()
        except ValueError:
            suffix = ""
    image_type = any(
        isinstance(value, str) and value.strip().lower().startswith("image/")
        for value in (content_type, declared_content_type)
    )
    if suffix not in _IMAGE_SUFFIXES and not image_type:
        return
    if not _is_image(content):
        raise InvalidImagePayload(
            "invalid_image_payload: unrecognized or truncated image container"
        )


def _is_image(data: bytes) -> bool:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _is_png(data)
    if data.startswith(b"\xff\xd8\xff"):
        return len(data) >= 12 and data.endswith(b"\xff\xd9")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return len(data) >= 14 and all(struct.unpack("<HH", data[6:10])) and data.endswith(b";")
    if data.startswith(b"RIFF"):
        return (
            len(data) >= 20
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") + 8 == len(data)
            and data[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
        )
    if data.startswith(b"BM"):
        return len(data) >= 26 and int.from_bytes(data[2:6], "little") == len(data)
    if data[:4] in (b"II*\0", b"MM\0*"):
        return len(data) >= 8
    if data.startswith(b"\0\0\x01\0"):
        count = int.from_bytes(data[4:6], "little")
        return count > 0 and len(data) >= 6 + count * 16
    if len(data) >= 24 and data[4:8] == b"ftyp":
        size = int.from_bytes(data[:4], "big")
        return 16 <= size <= len(data) and any(
            data[i : i + 4] in (b"avif", b"avis", b"heic", b"heix", b"mif1")
            for i in range(8, size, 4)
        )
    # Parse the root, rather than rejecting SVG foreignObject content containing HTML.
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return False
    try:
        root = ElementTree.fromstring(data)
        return root.tag in ("svg", "{http://www.w3.org/2000/svg}svg")
    except (ElementTree.ParseError, ValueError):
        return False


def _is_png(data: bytes) -> bool:
    offset = 8
    seen_header = False
    seen_data = False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        end = offset + 12 + size
        if end > len(data):
            return False
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : end - 4]
        checksum = int.from_bytes(data[end - 4 : end], "big")
        if zlib.crc32(data[offset + 4 : end - 4]) != checksum:
            return False
        if not seen_header:
            if kind != b"IHDR" or size != 13 or not all(struct.unpack(">II", payload[:8])):
                return False
            seen_header = True
        elif kind == b"IHDR":
            return False
        if kind == b"IDAT":
            seen_data = True
        if kind == b"IEND":
            return size == 0 and seen_data and end == len(data)
        offset = end
    return False
