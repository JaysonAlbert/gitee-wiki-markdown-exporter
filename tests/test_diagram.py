import pytest

from gitee_wiki_markdown_exporter.diagram import ChromeDiagramRenderer, DiagramRenderError


def test_invalid_diagram_xml_fails_before_starting_a_browser() -> None:
    renderer = ChromeDiagramRenderer(
        base_url="https://gitee.example.com",
        executable="/path/that/must/not/be/started",
    )

    with pytest.raises(DiagramRenderError, match="not valid XML"):
        renderer.render("<mxfile>")


def test_empty_diagram_component_has_a_sanitized_error() -> None:
    renderer = ChromeDiagramRenderer(base_url="https://gitee.example.com")

    with pytest.raises(DiagramRenderError, match="XML is empty"):
        renderer.render("  ")
