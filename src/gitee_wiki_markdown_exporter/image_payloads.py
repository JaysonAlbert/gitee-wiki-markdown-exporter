"""Conservative image signature/container checks, without decoding pixels."""

from __future__ import annotations

import re
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


def is_image_resource(name: str, *content_types: str | None) -> bool:
    """Recognize image intent from a filename/URL or declared content types."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        try:
            suffix = PurePosixPath(unquote(urlsplit(name).path)).suffix.lower()
        except ValueError:
            suffix = ""
    image_type = any(
        isinstance(value, str) and value.strip().lower().startswith("image/")
        for value in content_types
    )
    return suffix in _IMAGE_SUFFIXES or image_type


def validate_image_payload(
    content: bytes,
    *,
    name: str,
    content_type: str | None,
    declared_content_type: str | None = None,
) -> None:
    """Validate expected images; do not expose their content or URL in diagnostics."""
    if not is_image_resource(name, content_type, declared_content_type):
        return
    if not _is_image(content):
        raise InvalidImagePayload(f"invalid_image_payload: {_failure_reason(content)}")


def _is_image(data: bytes) -> bool:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _is_png(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _is_jpeg(data)
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
    try:
        root = ElementTree.fromstring(_safe_svg(data))
        return root.tag in ("svg", "{http://www.w3.org/2000/svg}svg")
    except (ElementTree.ParseError, ValueError):
        return False


_STANDARD_SVG_DTD = re.compile(
    rb"<!DOCTYPE\s+svg\s+PUBLIC\s+([\"'])-//W3C//DTD SVG "
    rb"(1\.[01])//EN\1\s+([\"'])https?://www\.w3\.org/"
    rb"(?:Graphics/SVG/1\.1/DTD/svg11\.dtd|TR/2001/REC-SVG-20010904/DTD/svg10\.dtd)\3\s*>",
)


def _safe_svg(data: bytes) -> bytes:
    # Restrict declarations before XML parsing, including UTF-16/32 encodings.
    # Standard public declarations are stripped, never fetched or resolved.
    if b"\x00" in data or b"<!ENTITY" in data.upper():
        raise ValueError("unsafe_svg")
    if b"<!DOCTYPE" in data.upper():
        cleaned, count = _STANDARD_SVG_DTD.subn(b"", data)
        if count != 1 or b"<!DOCTYPE" in cleaned.upper():
            raise ValueError("unsafe_svg")
        return cleaned
    return data


def _failure_reason(data: bytes) -> str:
    prefix = data.lstrip()[:512].lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "html_response"
    if (
        b"<!DOCTYPE" in data.upper()
        or b"<!ENTITY" in data.upper()
        or (b"\x00" in data and data.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")))
    ):
        try:
            _safe_svg(data)
        except ValueError:
            return "unsafe_svg"
    if data.startswith((b"PK\x03\x04", b"%PDF-", b"\xd0\xcf\x11\xe0")):
        return "image_type_mismatch"
    return "invalid_image_container"


def _is_jpeg(data: bytes) -> bool:
    """Walk marker lengths and scans; tolerate bytes after a genuine EOI marker."""
    offset = 2
    frame = False
    scan = False
    in_scan = False
    while offset < len(data):
        if in_scan:
            marker_start = data.find(b"\xff", offset)
            if marker_start < 0:
                return False
            offset = marker_start
        elif data[offset] != 0xFF:
            return False
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return False
        marker = data[offset]
        offset += 1
        if in_scan and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        in_scan = False
        if marker == 0xD9:
            return frame and scan
        if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7:
            return False
        if marker == 1:
            continue
        if offset + 2 > len(data):
            return False
        size = int.from_bytes(data[offset : offset + 2], "big")
        end = offset + size
        if size < 2 or end > len(data):
            return False
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if size < 8 or not all(struct.unpack(">HH", data[offset + 3 : offset + 7])):
                return False
            components = data[offset + 7]
            if not components or size != 8 + 3 * components:
                return False
            frame = True
        if marker == 0xDA:
            if not frame or size < 6 or size != 6 + 2 * data[offset + 2]:
                return False
            scan = True
            in_scan = True
        offset = end
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
