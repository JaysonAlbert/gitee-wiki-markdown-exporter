import json

import pytest

from gitee_wiki_markdown_exporter.rich_text import render_wiki_content


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
