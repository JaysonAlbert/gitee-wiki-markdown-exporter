"""Conservative current-revision attachment usage, independent of download success."""

from __future__ import annotations

import html
import re
from urllib.parse import unquote, urlsplit

from gitee_wiki_markdown_exporter.models import Attachment
from gitee_wiki_markdown_exporter.rich_text import (
    GITEE_MARKDOWN_SERIALIZER,
    _attachment_ids,
    _rich_text_document,
)

REFERENCE_VERSION = 1
REFERENCE_STATES = ("referenced", "unreferenced", "unknown")


def classify_attachment_references(
    content: str,
    attachments: tuple[Attachment, ...],
    base_url: str,
    markdown_destinations: tuple[str, ...],
) -> dict[str, str]:
    """Known references win; unsupported syntax never proves an attachment unused."""
    referenced: set[int] = set()
    urls: list[str] = []
    unknown = False
    document = _rich_text_document(content)

    def visit(node: object) -> None:
        nonlocal unknown
        if not isinstance(node, dict):
            unknown = True
            return
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in GITEE_MARKDOWN_SERIALIZER.nodes:
            unknown = True
        attrs = node.get("attrs") or {}
        if not isinstance(attrs, dict):
            unknown = True
            attrs = {}
        if kind == "image":
            if isinstance(attrs.get("src"), str):
                urls.append(attrs["src"])
            else:
                unknown = True
        elif kind == "attachments":
            selected = _attachment_ids(attrs.get("attachment-checked-list"))
            referenced.update(selected or (a.id for a in attachments))
        marks = node.get("marks", [])
        if not isinstance(marks, list):
            unknown = True
            marks = []
        for mark in marks:
            if not isinstance(mark, dict):
                unknown = True
                continue
            mark_type = mark.get("type")
            if not isinstance(mark_type, str) or mark_type not in GITEE_MARKDOWN_SERIALIZER.marks:
                unknown = True
            if mark_type == "link":
                value = mark.get("attrs")
                if isinstance(value, dict) and isinstance(value.get("href"), str):
                    urls.append(value["href"])
                else:
                    unknown = True
        children = node.get("content", [])
        if not isinstance(children, list):
            unknown = True
            return
        for child in children:
            visit(child)

    if document is not None:
        visit(document)
    else:
        urls.extend(markdown_destinations)
        # The existing Markdown scanner handles inline links, not HTML/reference links.
        unknown = bool(
            content.lstrip().startswith(("{", "["))
            and not markdown_destinations
            or re.search(r"(?m)^\s*\[[^\]]+\]:", content)
            or re.search(r"<[A-Za-z!/][^>]*>", content)
        )

    destinations = {
        path
        for url in urls
        for listed in (False, True)
        if (path := _resource_path(url, base_url, listed=listed)) is not None
    }
    result = {}
    for attachment in attachments:
        path = _resource_path(attachment.url, base_url, listed=True)
        if attachment.id in referenced or path is not None and path in destinations:
            result[str(attachment.id)] = "referenced"
        else:
            result[str(attachment.id)] = "unknown" if unknown or path is None else "unreferenced"
    return result


def _resource_path(url: str, base_url: str, *, listed: bool = False) -> str | None:
    try:
        value = re.sub(r"\\([\\()<> ])", r"\1", html.unescape(url))
        parsed = urlsplit(value)
        base = urlsplit(base_url)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme or base.scheme).lower() != base.scheme.lower() or (
                parsed.hostname,
                parsed.port or (443 if base.scheme == "https" else 80),
            ) != (base.hostname, base.port or (443 if base.scheme == "https" else 80)):
                return None
            if parsed.username or parsed.password:
                return None
        path = unquote(parsed.path)
        if listed and not parsed.scheme and not parsed.netloc:
            path = "/" + path.lstrip("/")
            if not path.startswith("/wiki-static/"):
                path = "/wiki-static" + path
        return path
    except ValueError:
        return None
