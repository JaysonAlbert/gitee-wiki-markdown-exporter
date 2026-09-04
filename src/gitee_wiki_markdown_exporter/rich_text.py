"""Serialize the observed Gitee Project Wiki ProseMirror schema to Markdown."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

RichTextNode = dict[str, Any]
NodeRenderer = Callable[["MarkdownSerializerState", RichTextNode], str]
MarkDelimiter = str | Callable[[RichTextNode], str]

_ESCAPABLE_TEXT = re.compile(r"[`*\\~\[\]_]")
_BLOCK_NODE_TYPES = {
    "blockquote",
    "bulletList",
    "codeBlock",
    "doc",
    "diagram",
    "heading",
    "horizontalRule",
    "layout",
    "layoutRow",
    "listItem",
    "orderedList",
    "paragraph",
    "table",
    "tableCell",
    "tableHeader",
    "tableRow",
}


@dataclass(frozen=True)
class MarkSerializerSpec:
    """Markdown delimiters and whitespace rules for one ProseMirror mark."""

    open: MarkDelimiter
    close: MarkDelimiter
    expel_enclosing_whitespace: bool = False
    escape: bool = True


class MarkdownSerializer:
    """Schema-driven ProseMirror JSON to Markdown serializer."""

    def __init__(
        self,
        nodes: dict[str, NodeRenderer],
        marks: dict[str, MarkSerializerSpec],
        *,
        strict: bool = False,
    ) -> None:
        self.nodes = nodes
        self.marks = marks
        self.strict = strict

    def serialize(
        self,
        document: RichTextNode,
        *,
        diagram_links: Mapping[int, tuple[str, ...]] | None = None,
    ) -> str:
        return MarkdownSerializerState(self, diagram_links=diagram_links).render_blocks(
            _children(document)
        )


class MarkdownSerializerState:
    """Per-document rendering state shared by registered node handlers."""

    def __init__(
        self,
        serializer: MarkdownSerializer,
        *,
        diagram_links: Mapping[int, tuple[str, ...]] | None = None,
    ) -> None:
        self.serializer = serializer
        self.diagram_links = diagram_links or {}
        self.at_line_start = True

    def render_blocks(self, nodes: list[RichTextNode]) -> str:
        blocks = [rendered for node in nodes if (rendered := self.render_block(node)).strip()]
        return "\n\n".join(blocks)

    def render_block(self, node: RichTextNode) -> str:
        node_type = node.get("type")
        renderer = self.serializer.nodes.get(node_type) if isinstance(node_type, str) else None
        if renderer is not None:
            return renderer(self, node)
        if self.serializer.strict:
            raise ValueError(f"Node type {node_type!r} is not supported by the Markdown renderer")
        children = _children(node)
        if not children:
            return ""
        if any(child.get("type") in _BLOCK_NODE_TYPES for child in children):
            return self.render_blocks(children)
        return self.render_inline(children)

    def render_inline(self, nodes: list[RichTextNode], *, at_line_start: bool = True) -> str:
        rendered: list[str] = []
        line_start = at_line_start
        for node in _coalesce_text_nodes(nodes):
            self.at_line_start = line_start
            node_type = node.get("type")
            renderer = self.serializer.nodes.get(node_type) if isinstance(node_type, str) else None
            if renderer is not None:
                value = renderer(self, node)
            elif self.serializer.strict:
                raise ValueError(
                    f"Node type {node_type!r} is not supported by the Markdown renderer"
                )
            else:
                value = self.render_inline(_children(node), at_line_start=line_start)
            rendered.append(value)
            if value:
                line_start = value.endswith("\n")
        self.at_line_start = line_start
        return "".join(rendered)

    def render_text(self, node: RichTextNode, *, at_line_start: bool = True) -> str:
        value = node.get("text", "")
        text = value if isinstance(value, str) else str(value)
        marks = _marks(node)
        mark_specs = [
            (mark, self.serializer.marks[mark_type])
            for mark in marks
            if (mark_type := _mark_type(mark)) in self.serializer.marks
            and _valid_mark(mark_type, mark)
        ]

        leading = trailing = ""
        if any(spec.expel_enclosing_whitespace for _, spec in mark_specs):
            match = re.match(r"^(\s*)(.*?)(\s*)$", text, flags=re.DOTALL)
            if match is not None:
                leading, text, trailing = match.groups()
            if not text:
                return f"{leading}{trailing}"

        unescaped_mark = next(((mark, spec) for mark, spec in mark_specs if not spec.escape), None)
        rendered = (
            _render_inline_code(text)
            if unescaped_mark is not None and _mark_type(unescaped_mark[0]) == "code"
            else self.escape(text, at_line_start)
        )

        for mark, spec in mark_specs:
            mark_type = _mark_type(mark)
            if mark_type == "code":
                continue
            rendered = f"{_delimiter(spec.open, mark)}{rendered}{_delimiter(spec.close, mark)}"
        return f"{leading}{rendered}{trailing}"

    def escape(self, text: str, at_line_start: bool = False) -> str:
        lines = text.split("\n")
        escaped: list[str] = []
        for index, line in enumerate(lines):
            value = _ESCAPABLE_TEXT.sub(lambda match: f"\\{match.group(0)}", line)
            if at_line_start or index > 0:
                value = re.sub(r"^(\+[ ]|[\-*>])", r"\\\1", value)
                value = re.sub(r"^(\s*)(#{1,6})(\s|$)", r"\1\\\2\3", value)
                value = re.sub(r"^(\s*\d+)\.\s", r"\1\\. ", value)
            escaped.append(value)
        return "\n".join(escaped)


def render_wiki_content(
    content: str,
    *,
    diagram_links: Mapping[int, tuple[str, ...]] | None = None,
) -> str:
    """Convert an observed Gitee rich-text document, preserving other text verbatim."""
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
    return GITEE_MARKDOWN_SERIALIZER.serialize(document, diagram_links=diagram_links)


def find_diagram_references(content: str) -> tuple[tuple[int, str | None], ...]:
    """Return unique draw.io component IDs and observed update timestamps in document order."""
    document = _rich_text_document(content)
    if document is None:
        return ()
    references: list[tuple[int, str | None]] = []
    seen: set[int] = set()

    def visit(node: RichTextNode) -> None:
        if node.get("type") == "diagram":
            attrs = _attrs(node)
            component_id = _int_attr(attrs.get("diagram-page-id") or attrs.get("diagramPageId"))
            if component_id is not None and component_id not in seen:
                updated = attrs.get("diagram-update-at") or attrs.get("diagramUpdateAt")
                references.append((component_id, str(updated) if updated is not None else None))
                seen.add(component_id)
        for child in _children(node):
            visit(child)

    visit(document)
    return tuple(references)


def _rich_text_document(content: str) -> RichTextNode | None:
    stripped = content.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    document = payload.get("default", payload)
    return document if isinstance(document, dict) and document.get("type") == "doc" else None


def _render_container(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return state.render_blocks(_children(node))


def _render_paragraph(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return state.render_inline(_children(node))


def _render_heading(state: MarkdownSerializerState, node: RichTextNode) -> str:
    raw_level = _attrs(node).get("level", 1)
    level = raw_level if isinstance(raw_level, int) else 1
    return f"{'#' * max(1, min(level, 6))} {state.render_inline(_children(node))}".rstrip()


def _render_bullet_list(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return _render_list(state, node, ordered=False)


def _render_ordered_list(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return _render_list(state, node, ordered=True)


def _render_list(state: MarkdownSerializerState, node: RichTextNode, *, ordered: bool) -> str:
    attrs = _attrs(node)
    start = attrs.get("start", attrs.get("order", 1))
    number = start if isinstance(start, int) else 1
    rendered: list[str] = []
    for child in _children(node):
        if child.get("type") != "listItem":
            fallback = state.render_block(child)
            if fallback:
                rendered.append(fallback)
            continue
        marker = f"{number}." if ordered else "-"
        rendered.append(_render_list_item_with_marker(state, child, marker=marker))
        number += 1
    return "\n".join(rendered)


def _render_list_item(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return _render_list_item_with_marker(state, node, marker="-")


def _render_list_item_with_marker(
    state: MarkdownSerializerState, node: RichTextNode, *, marker: str
) -> str:
    primary: list[str] = []
    nested: list[str] = []
    for child in _children(node):
        target = nested if child.get("type") in {"bulletList", "orderedList"} else primary
        value = state.render_block(child)
        if value:
            target.append(value)
    body = "\n\n".join(primary)
    lines = body.splitlines() or [""]
    result = [f"{marker} {lines[0]}"]
    indent = " " * (len(marker) + 1)
    result.extend(f"{indent}{line}" if line else "" for line in lines[1:])
    for value in nested:
        result.extend(f"{indent}{line}" if line else "" for line in value.splitlines())
    return "\n".join(result)


def _render_blockquote(state: MarkdownSerializerState, node: RichTextNode) -> str:
    body = state.render_blocks(_children(node))
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())


def _render_code_block(_state: MarkdownSerializerState, node: RichTextNode) -> str:
    code = _plain_text(_children(node))
    attrs = _attrs(node)
    language = attrs.get("language") or attrs.get("lang") or ""
    language = str(language).replace("`", "")
    fence = "`" * max(3, _longest_run(code, "`") + 1)
    return f"{fence}{language}\n{code.rstrip()}\n{fence}"


def _render_horizontal_rule(_state: MarkdownSerializerState, _node: RichTextNode) -> str:
    return "---"


def _render_table(state: MarkdownSerializerState, node: RichTextNode) -> str:
    rows: list[list[str]] = []
    for row in _children(node):
        if row.get("type") != "tableRow":
            continue
        rows.append([_render_table_cell(state, cell) for cell in _children(row)])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if width == 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [_table_row(normalized[0]), _table_row(["---"] * width)]
    lines.extend(_table_row(row) for row in normalized[1:])
    return "\n".join(lines)


def _render_table_container(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return state.render_blocks(_children(node))


def _render_table_cell(state: MarkdownSerializerState, node: RichTextNode) -> str:
    value = state.render_blocks(_children(node)).replace("\n", "<br>")
    return value.replace("|", "\\|")


def _table_row(cells: list[str]) -> str:
    return f"| {' | '.join(cells)} |"


def _render_text(state: MarkdownSerializerState, node: RichTextNode) -> str:
    return state.render_text(node, at_line_start=state.at_line_start)


def _render_hard_break(_state: MarkdownSerializerState, _node: RichTextNode) -> str:
    return "  \n"


def _render_image(state: MarkdownSerializerState, node: RichTextNode) -> str:
    attrs = _attrs(node)
    source = attrs.get("src") or attrs.get("url")
    if not source:
        return state.render_inline(_children(node), at_line_start=state.at_line_start)
    alt = state.escape(str(attrs.get("alt") or attrs.get("title") or ""))
    destination = _escape_link_destination(str(source))
    title = attrs.get("title")
    suffix = f' "{_escape_link_title(str(title))}"' if title else ""
    return f"![{alt}]({destination}{suffix})"


def _render_diagram(state: MarkdownSerializerState, node: RichTextNode) -> str:
    attrs = _attrs(node)
    component_id = _int_attr(attrs.get("diagram-page-id") or attrs.get("diagramPageId"))
    if component_id is None:
        return "> [!WARNING]\n> draw.io diagram was not exported."
    links = state.diagram_links.get(component_id, ())
    if not links:
        return f"> [!WARNING]\n> draw.io diagram {component_id} was not exported."
    return "\n\n".join(
        f"![draw.io diagram {page}]({_escape_link_destination(link)})"
        for page, link in enumerate(links, start=1)
    )


def _render_mention(_state: MarkdownSerializerState, node: RichTextNode) -> str:
    attrs = _attrs(node)
    label = attrs.get("text") or attrs.get("label") or attrs.get("name") or attrs.get("id")
    return f"@{label}" if label else ""


def _link_close(mark: RichTextNode) -> str:
    attrs = _attrs(mark)
    href = attrs.get("href")
    if not isinstance(href, str) or not href:
        return "]"
    title = attrs.get("title")
    suffix = f' "{_escape_link_title(str(title))}"' if title else ""
    return f"]({_escape_link_destination(href)}{suffix})"


def _delimiter(value: MarkDelimiter, mark: RichTextNode) -> str:
    return value(mark) if callable(value) else value


def _escape_link_destination(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace('"', '\\"')


def _escape_link_title(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_inline_code(text: str) -> str:
    fence = "`" * max(1, _longest_run(text, "`") + 1)
    padding = " " if "`" in text else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _coalesce_text_nodes(nodes: list[RichTextNode]) -> list[RichTextNode]:
    coalesced: list[RichTextNode] = []
    for node in nodes:
        if (
            coalesced
            and node.get("type") == "text"
            and coalesced[-1].get("type") == "text"
            and _marks(node) == _marks(coalesced[-1])
        ):
            previous = coalesced[-1]
            combined = dict(previous)
            combined["text"] = f"{previous.get('text', '')}{node.get('text', '')}"
            coalesced[-1] = combined
        else:
            coalesced.append(node)
    return coalesced


def _marks(node: RichTextNode) -> list[RichTextNode]:
    marks = node.get("marks", [])
    return [mark for mark in marks if isinstance(mark, dict)] if isinstance(marks, list) else []


def _mark_type(mark: RichTextNode) -> str:
    mark_type = mark.get("type")
    aliases = {"bold": "strong", "italic": "em", "strikethrough": "strike"}
    return aliases.get(mark_type, mark_type) if isinstance(mark_type, str) else ""


def _valid_mark(mark_type: str, mark: RichTextNode) -> bool:
    if mark_type != "link":
        return True
    href = _attrs(mark).get("href")
    return isinstance(href, str) and bool(href)


def _plain_text(nodes: list[RichTextNode]) -> str:
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


def _children(node: RichTextNode) -> list[RichTextNode]:
    content = node.get("content", [])
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, dict)]


def _attrs(node: RichTextNode) -> dict[str, Any]:
    attrs = node.get("attrs", {})
    return attrs if isinstance(attrs, dict) else {}


def _int_attr(value: object) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _longest_run(value: str, character: str) -> int:
    longest = current = 0
    for candidate in value:
        if candidate == character:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


GITEE_NODE_SERIALIZERS: dict[str, NodeRenderer] = {
    "blockquote": _render_blockquote,
    "bulletList": _render_bullet_list,
    "codeBlock": _render_code_block,
    "diagram": _render_diagram,
    "doc": _render_container,
    "hardBreak": _render_hard_break,
    "heading": _render_heading,
    "horizontalRule": _render_horizontal_rule,
    "image": _render_image,
    "layout": _render_container,
    "layoutRow": _render_container,
    "listItem": _render_list_item,
    "media": _render_image,
    "mediaSingle": _render_image,
    "mention": _render_mention,
    "orderedList": _render_ordered_list,
    "paragraph": _render_paragraph,
    "table": _render_table,
    "tableCell": _render_table_container,
    "tableHeader": _render_table_container,
    "tableRow": _render_table_container,
    "text": _render_text,
}

GITEE_MARK_SERIALIZERS = {
    "code": MarkSerializerSpec("", "", escape=False),
    "em": MarkSerializerSpec("_", "_", expel_enclosing_whitespace=True),
    "link": MarkSerializerSpec("[", _link_close),
    "strike": MarkSerializerSpec("~~", "~~", expel_enclosing_whitespace=True),
    "strong": MarkSerializerSpec("**", "**", expel_enclosing_whitespace=True),
    "underline": MarkSerializerSpec("<u>", "</u>"),
}

GITEE_MARKDOWN_SERIALIZER = MarkdownSerializer(
    GITEE_NODE_SERIALIZERS,
    GITEE_MARK_SERIALIZERS,
)
