from pathlib import Path

import pytest

from gitee_wiki_markdown_exporter.paths import render_diagram_path, render_page_path, safe_segment


def test_safe_segment_blocks_traversal_and_windows_reserved_names() -> None:
    assert safe_segment("../CON: roadmap?. ") == "__CON_ roadmap_"


def test_render_page_path_preserves_hierarchy_with_safe_segments() -> None:
    result = render_page_path(
        "{space_name}/{ancestor_titles}/{page_title}-{page_id}.md",
        space_name="Engineering",
        ancestors=("Backend/API", "Settlement"),
        page_title="Runbook: T+1",
        page_id=85455,
    )

    assert result == Path("Engineering/Backend_API/Settlement/Runbook_ T+1-85455.md")


def test_render_page_path_rejects_templates_that_escape_output() -> None:
    with pytest.raises(ValueError, match="relative path"):
        render_page_path(
            "../{page_title}.md",
            space_name="Engineering",
            ancestors=(),
            page_title="Runbook",
            page_id=1,
        )


def test_render_diagram_path_uses_stable_component_and_page_numbers() -> None:
    result = render_diagram_path(
        "{page_parent_path}/{page_title}/diagram-{diagram_id}-{diagram_page}.svg",
        page_path=Path("Engineering/Guides/Overview-7.md"),
        page_title="Overview",
        diagram_id=501,
        diagram_page=2,
    )

    assert result == Path("Engineering/Guides/Overview/diagram-501-2.svg")

    with pytest.raises(ValueError, match="relative path"):
        render_page_path(
            r"..\escape\{page_title}.md",
            space_name="Engineering",
            ancestors=(),
            page_title="Runbook",
            page_id=1,
        )
