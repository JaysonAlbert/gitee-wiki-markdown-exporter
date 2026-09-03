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
