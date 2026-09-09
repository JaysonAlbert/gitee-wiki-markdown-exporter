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
        'State: <span style="color: #008000;">**Ready**</span>\n\n'
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


@pytest.mark.parametrize(
    ("attrs", "style"),
    [
        ({"color": "rgb(255,0,0)", "backgroundColor": ""}, "color: #ff0000;"),
        ({"color": "#0000FF", "backgroundColor": None}, "color: #0000ff;"),
        (
            {"color": "#f00", "backgroundColor": "rgb(255, 255, 0)"},
            "color: #ff0000; background-color: #ffff00;",
        ),
        ({"color": "", "backgroundColor": "#abc"}, "background-color: #aabbcc;"),
    ],
)
def test_preserves_observed_text_style_colors(attrs: dict[str, object], style: str) -> None:
    content = json.dumps(
        {
            "default": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "注意 <标签> & ",
                                "marks": [{"type": "textStyle", "attrs": attrs}],
                            },
                            {
                                "type": "text",
                                "text": "后续",
                                "marks": [{"type": "textStyle", "attrs": attrs}],
                            },
                        ],
                    }
                ],
            }
        }
    )
    assert (
        render_wiki_content(content) == f'<span style="{style}">注意 &lt;标签&gt; &amp; 后续</span>'
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        1,
        {},
        "red; background:url(https://example.com)",
        '#fff" onclick="bad',
        "rgb(256,0,0)",
        "rgb(-1,0,0)",
        "var(--color)",
    ],
)
def test_invalid_colors_preserve_text_without_copying_css(value: object) -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Keep me",
                            "marks": [{"type": "textStyle", "attrs": {"color": value}}],
                        }
                    ],
                }
            ],
        }
    )
    assert render_wiki_content(content) == "Keep me"


def test_colors_compose_with_bold_links_code_and_table_cells() -> None:
    color = {"type": "textStyle", "attrs": {"color": "#f00"}}
    paragraph = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "bold", "marks": [{"type": "bold"}, color]},
            {
                "type": "text",
                "text": "link",
                "marks": [{"type": "link", "attrs": {"href": "#anchor"}}, color],
            },
            {"type": "text", "text": "<code>", "marks": [{"type": "code"}, color]},
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
                            "content": [{"type": "tableCell", "content": [paragraph]}],
                        }
                    ],
                }
            ],
        }
    )
    assert render_wiki_content(content) == (
        '| <span style="color: #ff0000;">**bold**</span>'
        '<span style="color: #ff0000;">[link](#anchor)</span>'
        '<span style="color: #ff0000;">`<code>`</span> |\n| --- |'
    )


def test_status_keeps_literal_color_and_escapes_title() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "status", "attrs": {"title": "Ready <now>", "color": "green"}}
                    ],
                }
            ],
        }
    )
    assert (
        render_wiki_content(content) == '<span style="color: #008000;">**Ready &lt;now&gt;**</span>'
    )


def test_explicit_image_title_becomes_visible_caption_without_using_alt_text() -> None:
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "image",
                            "attrs": {"src": "image.png", "alt": "diagram", "title": "A < B"},
                        }
                    ],
                }
            ],
        }
    )
    assert render_wiki_content(content) == '![diagram](image.png "A < B")<br><em>A &lt; B</em>'
    without_title = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "image", "attrs": {"src": "image.png", "alt": "diagram"}}],
                }
            ],
        }
    )
    assert render_wiki_content(without_title) == "![diagram](image.png)"
