"""Safe output path rendering."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LEADING_TRAVERSAL = re.compile(r"^\.+[/\\]*")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def safe_segment(value: str, *, max_length: int = 120) -> str:
    """Return a cross-platform safe path segment."""
    if max_length < 16:
        raise ValueError("max_length must be at least 16")
    normalized = unicodedata.normalize("NFC", value)
    normalized = _LEADING_TRAVERSAL.sub("__", normalized)
    normalized = _FORBIDDEN.sub("_", normalized).strip().rstrip(". ")
    if not normalized:
        normalized = "_"
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        normalized = "_" + normalized
    if len(normalized) > max_length:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized[: max_length - 11]}-{digest}"
    return normalized


def render_page_path(
    template: str,
    *,
    space_name: str,
    ancestors: tuple[str, ...],
    page_title: str,
    page_id: int,
) -> Path:
    """Render one relative page path from trusted placeholders."""
    values = {
        "space_name": safe_segment(space_name),
        "ancestor_titles": "/".join(safe_segment(title) for title in ancestors),
        "page_title": safe_segment(page_title),
        "page_id": page_id,
    }
    try:
        rendered = template.format_map(values)
    except KeyError as error:
        raise ValueError(f"unsupported page path placeholder: {error.args[0]}") from error
    rendered = re.sub(r"/{2,}", "/", rendered)
    if rendered.startswith("./"):
        rendered = rendered[2:]
    relative = PurePosixPath(rendered)
    windows_path = PureWindowsPath(rendered)
    if (
        not rendered
        or "\\" in rendered
        or relative.is_absolute()
        or ".." in relative.parts
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in windows_path.parts
    ):
        raise ValueError("page template must render a safe relative path")
    return Path(*relative.parts)


def render_attachment_path(
    template: str,
    *,
    page_path: Path,
    page_title: str,
    attachment_id: int,
    attachment_name: str,
) -> Path:
    """Render one attachment path relative to the output root."""
    suffix = Path(attachment_name).suffix
    values = {
        "page_parent_path": page_path.parent.as_posix(),
        "page_title": safe_segment(page_title),
        "attachment_file_id": attachment_id,
        "attachment_extension": f".{safe_segment(suffix.lstrip('.'))}" if suffix else "",
        "attachment_name": safe_segment(attachment_name),
    }
    try:
        rendered = template.format_map(values)
    except KeyError as error:
        raise ValueError(f"unsupported attachment path placeholder: {error.args[0]}") from error
    rendered = re.sub(r"/{2,}", "/", rendered)
    relative = PurePosixPath(rendered)
    windows_path = PureWindowsPath(rendered)
    if (
        not rendered
        or "\\" in rendered
        or relative.is_absolute()
        or ".." in relative.parts
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in windows_path.parts
    ):
        raise ValueError("attachment template must render a safe relative path")
    return Path(*relative.parts)


def render_diagram_path(
    template: str,
    *,
    page_path: Path,
    page_title: str,
    diagram_id: int,
    diagram_page: int,
) -> Path:
    """Render one SVG diagram path relative to the output root."""
    values = {
        "page_parent_path": page_path.parent.as_posix(),
        "page_title": safe_segment(page_title),
        "diagram_id": diagram_id,
        "diagram_page": diagram_page,
    }
    try:
        rendered = template.format_map(values)
    except KeyError as error:
        raise ValueError(f"unsupported diagram path placeholder: {error.args[0]}") from error
    rendered = re.sub(r"/{2,}", "/", rendered)
    relative = PurePosixPath(rendered)
    windows_path = PureWindowsPath(rendered)
    if (
        not rendered
        or "\\" in rendered
        or relative.is_absolute()
        or ".." in relative.parts
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in windows_path.parts
    ):
        raise ValueError("diagram template must render a safe relative path")
    return Path(*relative.parts)
