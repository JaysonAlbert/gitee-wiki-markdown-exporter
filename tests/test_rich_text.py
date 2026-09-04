import json

import pytest

from gitee_wiki_markdown_exporter.rich_text import find_diagram_references, render_wiki_content


@pytest.mark.parametrize(
    "content",
    [
        "# Already Markdown\n",
        "{not valid JSON}",
        '{"kind":"unrecognized"}',
    ],
)
def test_unrecognized_content_passes_through_unchanged(content: str) -> None:
    assert render_wiki_content(content) == content


def test_renders_ordered_list_blockquote_and_code_block() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "orderedList",
                    "attrs": {"start": 3},
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Third"}],
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Quoted"}],
                        }
                    ],
                },
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('ok')"}],
                },
            ],
        }
    )

    assert render_wiki_content(content) == ("3. Third\n\n> Quoted\n\n```python\nprint('ok')\n```")


def test_escapes_plain_text_that_would_change_markdown_structure() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "# not a heading\n1. not a list\n*literal emphasis*",
                        }
                    ],
                }
            ],
        }
    )

    assert render_wiki_content(content) == (
        "\\# not a heading\n1\\. not a list\n\\*literal emphasis\\*"
    )


def test_serializes_fragmented_marks_as_one_whitespace_safe_span() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": " leading ",
                            "marks": [{"type": "bold"}],
                        },
                        {
                            "type": "text",
                            "text": "together ",
                            "marks": [{"type": "bold"}],
                        },
                        {"type": "text", "text": "after"},
                    ],
                }
            ],
        }
    )

    assert render_wiki_content(content) == " **leading together** after"


def test_serializes_inline_code_and_link_delimiters_safely() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "`",
                            "marks": [{"type": "code"}],
                        },
                        {"type": "text", "text": " and "},
                        {
                            "type": "text",
                            "text": "docs",
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {
                                        "href": "https://example.com/a(b)",
                                        "title": 'A "title"',
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )

    assert render_wiki_content(content) == (
        '`` ` `` and [docs](https://example.com/a\\(b\\) "A \\"title\\"")'
    )


def test_extracts_and_renders_multi_page_diagram_as_svg_links() -> None:
    content = json.dumps(
        {
            "default": {
                "type": "doc",
                "content": [
                    {
                        "type": "diagram",
                        "attrs": {
                            "diagram-page-id": "501",
                            "diagram-update-at": "2026-01-02T03:04:05Z",
                        },
                    }
                ],
            }
        }
    )

    assert find_diagram_references(content) == ((501, "2026-01-02T03:04:05Z"),)
    assert render_wiki_content(
        content,
        diagram_links={501: ("Overview/diagram-501-1.svg", "Overview/diagram-501-2.svg")},
    ) == (
        "![draw.io diagram 1](Overview/diagram-501-1.svg)\n\n"
        "![draw.io diagram 2](Overview/diagram-501-2.svg)"
    )


def test_missing_diagram_svg_is_visible_in_markdown() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [{"type": "diagram", "attrs": {"diagram-page-id": 501}}],
        }
    )

    assert render_wiki_content(content) == "> [!WARNING]\n> draw.io diagram 501 was not exported."


def test_renders_observed_task_status_info_directory_and_attachment_nodes() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Getting Started"}],
                },
                {"type": "directory", "attrs": {"directory-display-level": ""}},
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "State: "},
                        {"type": "status", "attrs": {"title": "Ready", "color": "green"}},
                    ],
                },
                {
                    "type": "infoBlock",
                    "attrs": {"info-block-icon": "info"},
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Read this first."}],
                        }
                    ],
                },
                {
                    "type": "taskList",
                    "content": [
                        {
                            "type": "taskItem",
                            "attrs": {"checked": True},
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Installed"}],
                                }
                            ],
                        },
                        {
                            "type": "taskItem",
                            "attrs": {"checked": False},
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Configured"}],
                                }
                            ],
                        },
                    ],
                },
                {
                    "type": "attachments",
                    "attrs": {"attachment-checked-list": "99,100"},
                },
            ],
        }
    )

    assert render_wiki_content(
        content,
        attachment_links={99: ("guide.pdf", "Home/99.pdf")},
    ) == (
        "## Getting Started\n\n"
        "- [Getting Started](#getting-started)\n\n"
        "State: **Ready**\n\n"
        "> [!NOTE]\n> Read this first.\n\n"
        "- [x] Installed\n- [ ] Configured\n\n"
        "- [guide.pdf](Home/99.pdf)\n"
        "- Attachment 100 was not exported."
    )


def test_expands_table_spans_without_shifting_following_cells() -> None:
    def cell(value: str, **attrs: int) -> dict[str, object]:
        return {
            "type": "tableCell",
            "attrs": attrs,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": value}],
                }
            ],
        }

    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [cell("A", colspan=2), cell("C")],
                        },
                        {
                            "type": "tableRow",
                            "content": [cell("X", rowspan=2), cell("Y"), cell("Z")],
                        },
                        {
                            "type": "tableRow",
                            "content": [cell("P"), cell("Q")],
                        },
                    ],
                }
            ],
        }
    )

    assert render_wiki_content(content) == (
        "| A |  | C |\n| --- | --- | --- |\n| X | Y | Z |\n|  | P | Q |"
    )
