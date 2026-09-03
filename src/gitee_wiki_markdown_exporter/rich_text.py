"""Render observed Gitee Project Wiki rich-text JSON as Markdown."""

from __future__ import annotations

import json
from typing import Any


def render_wiki_content(content: str) -> str:
    """Convert an observed ProseMirror-style document, preserving plain text verbatim."""
    stripped = content.strip()
    if not stripped.startswith("{"):
        return content
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(payload, dict):
        return content
    document = payload.get("default", payload)
    if not isinstance(document, dict) or document.get("type") != "doc":
        return content
    return _render_blocks(_children(document))


def _render_blocks(nodes: list[dict[str, Any]]) -> str:
    blocks = [rendered for node in nodes if (rendered := _render_block(node)).strip()]
    return "\n\n".join(blocks)


def _render_block(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    children = _children(node)
    if node_type in {"doc", "layout", "layoutRow"}:
        return _render_blocks(children)
    if node_type == "paragraph":
        return _render_inline(children)
    if node_type == "heading":
        attrs = _attrs(node)
        raw_level = attrs.get("level", 1)
        level = raw_level if isinstance(raw_level, int) else 1
        return f"{'#' * max(1, min(level, 6))} {_render_inline(children)}".rstrip()
    if node_type in {"bulletList", "orderedList"}:
        return _render_list(node, ordered=node_type == "orderedList")
    if node_type == "listItem":
        return _render_list_item(node, marker="-")
    if node_type == "blockquote":
        body = _render_blocks(children)
        return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
    if node_type == "codeBlock":
        code = _plain_text(children)
        attrs = _attrs(node)
        language = attrs.get("language") or attrs.get("lang") or ""
        language = str(language).replace("`", "")
        fence = "`" * max(3, _longest_run(code, "`") + 1)
        return f"{fence}{language}\n{code.rstrip()}\n{fence}"
    if node_type == "horizontalRule":
        return "---"
    if node_type == "table":
        return _render_table(node)
    if node_type in {"tableRow", "tableCell", "tableHeader"}:
        return _render_blocks(children)
    if node_type == "text":
        return _render_text(node)
    return _render_blocks(children) if children else _render_inline_node(node)


def _render_inline(nodes: list[dict[str, Any]]) -> str:
    return "".join(_render_inline_node(node) for node in nodes)


def _render_inline_node(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if node_type == "text":
        return _render_text(node)
    if node_type == "hardBreak":
        return "  \n"
    if node_type in {"image", "media", "mediaSingle"}:
        attrs = _attrs(node)
        source = attrs.get("src") or attrs.get("url")
        if source:
            alt = str(attrs.get("alt") or attrs.get("title") or "")
            return f"![{alt}]({source})"
    if node_type == "mention":
        attrs = _attrs(node)
        label = attrs.get("text") or attrs.get("label") or attrs.get("name") or attrs.get("id")
        return f"@{label}" if label else ""
    children = _children(node)
    return _render_inline(children) if children else ""


def _render_text(node: dict[str, Any]) -> str:
    value = node.get("text", "")
    text = value if isinstance(value, str) else str(value)
    marks = node.get("marks", [])
    if not isinstance(marks, list):
        return text

    link: str | None = None
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        mark_type = mark.get("type")
        if mark_type == "code":
            fence = "`" * max(1, _longest_run(text, "`") + 1)
            text = f"{fence}{text}{fence}"
        elif mark_type in {"bold", "strong"}:
            text = f"**{text}**"
        elif mark_type in {"italic", "em"}:
            text = f"_{text}_"
        elif mark_type in {"strike", "strikethrough"}:
            text = f"~~{text}~~"
        elif mark_type == "underline":
            text = f"<u>{text}</u>"
        elif mark_type == "link":
            href = _attrs(mark).get("href")
            if isinstance(href, str) and href:
                link = href
    return f"[{text}]({link})" if link else text


def _render_list(node: dict[str, Any], *, ordered: bool) -> str:
    attrs = _attrs(node)
    start = attrs.get("start", attrs.get("order", 1))
    number = start if isinstance(start, int) else 1
    rendered: list[str] = []
    for child in _children(node):
        if child.get("type") != "listItem":
            fallback = _render_block(child)
            if fallback:
                rendered.append(fallback)
            continue
        marker = f"{number}." if ordered else "-"
        rendered.append(_render_list_item(child, marker=marker))
        number += 1
    return "\n".join(rendered)


def _render_list_item(node: dict[str, Any], *, marker: str) -> str:
    children = _children(node)
    primary: list[str] = []
    nested: list[str] = []
    for child in children:
        if child.get("type") in {"bulletList", "orderedList"}:
            value = _render_block(child)
            if value:
                nested.append(value)
        else:
            value = _render_block(child)
            if value:
                primary.append(value)
    body = "\n\n".join(primary)
    lines = body.splitlines() or [""]
    result = [f"{marker} {lines[0]}"]
    result.extend(f"  {line}" if line else "" for line in lines[1:])
    for value in nested:
        result.extend(f"  {line}" if line else "" for line in value.splitlines())
    return "\n".join(result)


def _render_table(node: dict[str, Any]) -> str:
    rows: list[list[str]] = []
    for row in _children(node):
        if row.get("type") != "tableRow":
            continue
        cells = [_render_table_cell(cell) for cell in _children(row)]
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if width == 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [_table_row(normalized[0]), _table_row(["---"] * width)]
    lines.extend(_table_row(row) for row in normalized[1:])
    return "\n".join(lines)


def _render_table_cell(node: dict[str, Any]) -> str:
    value = _render_blocks(_children(node)).replace("\n", "<br>")
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _table_row(cells: list[str]) -> str:
    return f"| {' | '.join(cells)} |"


def _plain_text(nodes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.get("type") == "text":
            value = node.get("text", "")
            parts.append(value if isinstance(value, str) else str(value))
        elif node.get("type") == "hardBreak":
            parts.append("\n")
        else:
            parts.append(_plain_text(_children(node)))
    return "".join(parts)


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    content = node.get("content", [])
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, dict)]


def _attrs(node: dict[str, Any]) -> dict[str, Any]:
    attrs = node.get("attrs", {})
    return attrs if isinstance(attrs, dict) else {}


def _longest_run(value: str, character: str) -> int:
    longest = current = 0
    for candidate in value:
        if candidate == character:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
