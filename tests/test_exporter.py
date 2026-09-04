import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gitee_wiki_markdown_exporter.client import GiteeWikiError
from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.diagram import DiagramRenderError
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter, _replace_directory
from gitee_wiki_markdown_exporter.models import (
    Attachment,
    DiagramComponent,
    PageCandidate,
    PageRevision,
    Space,
    TreeNode,
)


class FakeWikiClient:
    base_url = "https://gitee.example.com"

    def __init__(self) -> None:
        self.tree = (
            TreeNode(
                1,
                "Home",
                "root",
                (TreeNode(2, "Runbook", 1),),
            ),
        )
        self.revisions = {1: 10, 2: 20}
        self.bodies = {
            1: "Welcome",
            2: "![diagram](/wiki-static/demo/2/diagram.png)",
        }
        self.revision_reads: list[int] = []
        self.attachment_reads: list[int] = []
        self.download_reads: list[str] = []
        self.attachments = {
            1: (),
            2: (Attachment(99, "diagram.png", "demo/2/diagram.png", size=3),),
        }
        self.fail_page: int | None = None
        self.fail_attachment_urls: set[str] = set()
        self.diagram_components = {
            501: DiagramComponent(501, '<mxfile><diagram id="one"/></mxfile>')
        }
        self.diagram_reads: list[int] = []
        self.fail_diagram_ids: set[int] = set()
        self.direct_pages: dict[int, object] = {}

    def get_space(self, space_key: str) -> Space:
        return Space(34, space_key, "Engineering")

    def get_tree(self, _space_id: int) -> tuple[TreeNode, ...]:
        return self.tree

    def get_page(self, _space_id: int, page_id: int):
        page = self.direct_pages.get(page_id)
        if page is not None:
            return page

        def find(
            nodes: tuple[TreeNode, ...],
            ancestors: tuple[str, ...] = (),
            ancestor_ids: tuple[int, ...] = (),
        ):
            for node in nodes:
                if node.page_id == page_id:
                    return PageCandidate(
                        node.page_id,
                        node.title,
                        node.parent_id,
                        ancestors,
                        ancestor_ids,
                    )
                found = find(
                    node.children,
                    (*ancestors, node.title),
                    (*ancestor_ids, node.page_id),
                )
                if found is not None:
                    return found
            return None

        page = find(self.tree)
        if page is None:
            raise GiteeWikiError(f"Wiki page {page_id} was not found")
        return page

    def latest_revision(self, _space_id: int, page_id: int) -> int:
        return self.revisions[page_id]

    def get_revision(self, _space_id: int, page_id: int, revision_id: int) -> PageRevision:
        if page_id == self.fail_page:
            raise RuntimeError("simulated remote failure")
        self.revision_reads.append(page_id)
        title = "Home" if page_id == 1 else "Runbook"
        return PageRevision(revision_id, title, "text", self.bodies[page_id])

    def list_attachments(self, page_id: int) -> tuple[Attachment, ...]:
        self.attachment_reads.append(page_id)
        return self.attachments[page_id]

    def download_attachment(self, url: str, *, max_bytes: int) -> tuple[bytes, str]:
        assert max_bytes >= 3
        self.download_reads.append(url)
        if url in self.fail_attachment_urls:
            raise GiteeWikiError(
                "GET https://gitee.example.com/wiki-static/failed failed: HTTP 500"
            )
        return url.encode("utf-8")[-3:], "image/png"

    def get_diagram_component(self, _space_key: str, component_page_id: int) -> DiagramComponent:
        self.diagram_reads.append(component_page_id)
        if component_page_id in self.fail_diagram_ids:
            raise GiteeWikiError("GET https://gitee.example.com/component failed: HTTP 503")
        return self.diagram_components[component_page_id]


class FakeDiagramRenderer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def render(self, xml: str) -> tuple[str, ...]:
        self.calls.append(xml)
        if self.fail:
            raise DiagramRenderError("local browser could not render the diagram")
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"></svg>',
        )


def diagram_body(component_id: int = 501, updated_at: str = "2026-01-02T03:04:05Z") -> str:
    return json.dumps(
        {
            "default": {
                "type": "doc",
                "content": [
                    {
                        "type": "diagram",
                        "attrs": {
                            "diagram-page-id": component_id,
                            "diagram-update-at": updated_at,
                        },
                    }
                ],
            }
        }
    )


def settings(output: Path) -> ExportSettings:
    return ExportSettings(output_path=output)


def test_first_sync_exports_tree_localizes_attachments_and_writes_manifest(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"

    result = WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert result.updated == 2
    runbook = output / "Engineering/Home/Runbook-2.md"
    assert runbook.read_text(encoding="utf-8") == "# Runbook\n\n![diagram](Runbook/99.png)\n"
    assert (output / "Engineering/Home/Runbook/99.png").read_bytes() == b"png"
    manifest = json.loads((output / "gitee-wiki-lock.json").read_text(encoding="utf-8"))
    assert manifest["spaces"]["ENG"]["pages"]["2"]["revision"] == "20"


def test_rich_text_revision_is_rendered_as_markdown(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.bodies[1] = json.dumps(
        {
            "default": {
                "type": "doc",
                "content": [
                    {
                        "type": "heading",
                        "attrs": {"level": 2},
                        "content": [{"type": "text", "text": "Overview"}],
                    },
                    {
                        "type": "layout",
                        "content": [
                            {
                                "type": "layoutRow",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "Important",
                                                "marks": [{"type": "bold"}],
                                            },
                                            {"type": "text", "text": " "},
                                            {
                                                "type": "text",
                                                "text": "Docs",
                                                "marks": [
                                                    {
                                                        "type": "link",
                                                        "attrs": {
                                                            "href": "https://example.com/docs"
                                                        },
                                                    }
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "bulletList",
                        "content": [
                            {
                                "type": "listItem",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "First"}],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "table",
                        "content": [
                            {
                                "type": "tableRow",
                                "content": [
                                    {
                                        "type": "tableCell",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": "Name"}],
                                            }
                                        ],
                                    },
                                    {
                                        "type": "tableCell",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": "Owner"}],
                                            }
                                        ],
                                    },
                                ],
                            },
                            {
                                "type": "tableRow",
                                "content": [
                                    {
                                        "type": "tableCell",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": "Widget"}],
                                            }
                                        ],
                                    },
                                    {
                                        "type": "tableCell",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": "Team"}],
                                            }
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }
        }
    )

    WikiExporter(client=client, settings=settings(tmp_path / "mirror")).sync_pages("ENG", (1,))

    assert (tmp_path / "mirror/Engineering/Home-1.md").read_text(encoding="utf-8") == (
        "# Home\n\n"
        "## Overview\n\n"
        "**Important** [Docs](https://example.com/docs)\n\n"
        "- First\n\n"
        "| Name | Owner |\n"
        "| --- | --- |\n"
        "| Widget | Team |\n"
    )


def test_drawio_diagram_is_mirrored_only_as_svg_and_reused_incrementally(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)

    first = exporter.sync_pages("ENG", (1,))

    assert first.status == "ok"
    page = output / "Engineering/Home-1.md"
    assert page.read_text(encoding="utf-8") == (
        "# Home\n\n"
        "![draw.io diagram 1](Home/diagram-501-1.svg)\n\n"
        "![draw.io diagram 2](Home/diagram-501-2.svg)\n"
    )
    assert (output / "Engineering/Home/diagram-501-1.svg").is_file()
    assert (output / "Engineering/Home/diagram-501-2.svg").is_file()
    assert not list(output.rglob("*.drawio"))
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 1

    client.revision_reads.clear()
    client.diagram_reads.clear()
    second = exporter.sync_pages("ENG", (1,))

    assert second.unchanged == 1
    assert client.revision_reads == []
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 1


def test_changed_page_reuses_unchanged_diagram_by_component_hash(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)
    exporter.sync_pages("ENG", (1,))
    client.revisions[1] = 11
    client.bodies[1] = diagram_body(updated_at="2026-01-03T03:04:05Z")
    client.diagram_reads.clear()

    result = exporter.sync_pages("ENG", (1,))

    assert result.updated == 1
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 1


def test_diagram_component_change_is_detected_without_a_page_revision_change(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)
    exporter.sync_pages("ENG", (1,))
    client.diagram_components[501] = DiagramComponent(
        501, '<mxfile><diagram id="independent-change"/></mxfile>'
    )
    client.diagram_reads.clear()

    result = exporter.sync_pages("ENG", (1,))

    assert result.updated == 1
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 2


def test_failed_diagram_is_partial_and_retried_without_persisting_xml(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    renderer.fail = True
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)

    first = exporter.sync_pages("ENG", (1,))

    assert first.status == "partial"
    assert first.errors == (
        "page 1 diagram 501 skipped: local browser could not render the diagram",
    )
    assert "draw.io diagram 501 was not exported" in (output / "Engineering/Home-1.md").read_text(
        encoding="utf-8"
    )
    assert '<mxfile><diagram id="one"' not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file()
    )
    manifest = json.loads((output / "gitee-wiki-lock.json").read_text(encoding="utf-8"))
    assert manifest["spaces"]["ENG"]["pages"]["1"]["diagramsComplete"] is False

    renderer.fail = False
    client.diagram_reads.clear()
    retry = exporter.sync_pages("ENG", (1,))

    assert retry.status == "ok"
    assert client.diagram_reads == [501]
    assert (output / "Engineering/Home/diagram-501-1.svg").is_file()


def test_transient_diagram_failure_preserves_last_successful_svg_and_retries(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)
    exporter.sync_pages("ENG", (1,))
    original_document = (output / "Engineering/Home-1.md").read_text(encoding="utf-8")
    original_svg = (output / "Engineering/Home/diagram-501-1.svg").read_text(encoding="utf-8")
    client.fail_diagram_ids.add(501)

    failed = exporter.sync_pages("ENG", (1,))

    assert failed.status == "partial"
    assert (output / "Engineering/Home-1.md").read_text(encoding="utf-8") == original_document
    assert (output / "Engineering/Home/diagram-501-1.svg").read_text(
        encoding="utf-8"
    ) == original_svg
    manifest = json.loads((output / "gitee-wiki-lock.json").read_text(encoding="utf-8"))
    assert manifest["spaces"]["ENG"]["pages"]["1"]["diagramsComplete"] is False

    client.fail_diagram_ids.clear()
    client.diagram_reads.clear()
    retry = exporter.sync_pages("ENG", (1,))

    assert retry.status == "ok"
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 1


def test_changed_diagram_component_rerenders_and_removed_diagram_cleans_svg(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)
    exporter.sync_pages("ENG", (1,))
    client.revisions[1] = 11
    client.diagram_components[501] = DiagramComponent(
        501, '<mxfile><diagram id="changed"/></mxfile>'
    )

    changed = exporter.sync_pages("ENG", (1,))

    assert changed.updated == 1
    assert len(renderer.calls) == 2
    client.revisions[1] = 12
    client.bodies[1] = "Diagram removed"

    removed = exporter.sync_pages("ENG", (1,))

    assert removed.updated == 1
    assert not list(output.rglob("*.svg"))


def test_page_move_relocates_diagram_svgs_without_rerendering(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.bodies[1] = diagram_body()
    renderer = FakeDiagramRenderer()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output), diagram_renderer=renderer)
    exporter.sync_pages("ENG", (1,))
    client.direct_pages[1] = PageCandidate(
        page_id=1,
        title="Home",
        parent_id=9,
        ancestors=("Moved",),
        ancestor_ids=(9,),
    )
    client.diagram_reads.clear()

    moved = exporter.sync_pages("ENG", (1,))

    assert moved.moved == 1
    assert client.diagram_reads == [501]
    assert len(renderer.calls) == 1
    assert (output / "Engineering/Moved/Home/diagram-501-1.svg").is_file()
    document = (output / "Engineering/Moved/Home-1.md").read_text(encoding="utf-8")
    assert "Home/diagram-501-1.svg" in document
    assert not (output / "Engineering/Home/diagram-501-1.svg").exists()


def test_legacy_manifest_page_is_rerendered_without_revision_change(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_pages("ENG", (1,))
    page_path = output / "Engineering/Home-1.md"
    page_path.write_text('# Home\n\n{"default":{"type":"doc"}}\n', encoding="utf-8")
    manifest_path = output / "gitee-wiki-lock.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spaces"]["ENG"]["pages"]["1"].pop("rendererVersion")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client.bodies[1] = json.dumps(
        {
            "default": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Converted"}],
                    }
                ],
            }
        }
    )
    client.revision_reads.clear()

    result = exporter.sync_pages("ENG", (1,))

    assert result.updated == 1
    assert client.revision_reads == [1]
    assert page_path.read_text(encoding="utf-8") == "# Home\n\nConverted\n"


def test_previous_renderer_version_is_rerendered_without_revision_change(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_pages("ENG", (1,))
    manifest_path = output / "gitee-wiki-lock.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spaces"]["ENG"]["pages"]["1"]["rendererVersion"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client.revision_reads.clear()

    result = exporter.sync_pages("ENG", (1,))

    assert result.updated == 1
    assert client.revision_reads == [1]


def test_manifest_does_not_persist_attachment_query_credentials(tmp_path: Path) -> None:
    client = FakeWikiClient()
    signed_url = "demo/2/diagram.png?sig=new-signed-secret"
    client.attachments[2] = (Attachment(99, "diagram.png", signed_url, size=3),)
    client.bodies[2] = "![diagram](/wiki-static/demo/2/diagram.png?sig=old-signed-secret)"
    output = tmp_path / "mirror"

    WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    manifest_text = (output / "gitee-wiki-lock.json").read_text(encoding="utf-8")
    assert "signed-secret" not in manifest_text
    manifest = json.loads(manifest_text)
    attachment = manifest["spaces"]["ENG"]["pages"]["2"]["attachments"][0]
    assert attachment["urlPath"] == "/wiki-static/demo/2/diagram.png"
    assert "url" not in attachment
    document = (output / "Engineering/Home/Runbook-2.md").read_text(encoding="utf-8")
    assert document == "# Runbook\n\n![diagram](Runbook/99.png)\n"
    assert "signed-secret" not in document


def test_failed_attachment_is_reported_and_page_export_continues(tmp_path: Path) -> None:
    client = FakeWikiClient()
    failed_url = "demo/2/diagram.png?sig=sensitive-value"
    client.attachments[2] = (Attachment(99, "diagram.png", failed_url, size=3),)
    client.bodies[2] = f"![diagram](/wiki-static/{failed_url})"
    client.fail_attachment_urls.add(failed_url)
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))

    result = exporter.sync_spaces(("ENG",))

    assert result.status == "partial"
    assert result.errors == (
        "page 2 attachment 99 skipped: "
        "GET https://gitee.example.com/wiki-static/failed failed: HTTP 500",
    )
    assert (output / "Engineering/Home/Runbook-2.md").read_text(encoding="utf-8") == (
        "# Runbook\n\n![diagram](https://gitee.example.com/wiki-static/demo/2/diagram.png)\n"
    )
    assert not (output / "Engineering/Home/Runbook/99.png").exists()
    manifest = json.loads((output / "gitee-wiki-lock.json").read_text(encoding="utf-8"))
    assert manifest["spaces"]["ENG"]["pages"]["2"]["attachments"] == []

    client.fail_attachment_urls.clear()
    client.download_reads.clear()
    retry_result = exporter.sync_spaces(("ENG",))

    assert retry_result.status == "ok"
    assert client.download_reads == [failed_url]
    assert (output / "Engineering/Home/Runbook/99.png").read_bytes() == b"lue"
    assert (output / "Engineering/Home/Runbook-2.md").read_text(encoding="utf-8") == (
        "# Runbook\n\n![diagram](Runbook/99.png)\n"
    )


def test_second_sync_polls_metadata_but_skips_unchanged_page_and_attachment_bytes(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.revision_reads.clear()
    client.attachment_reads.clear()
    client.download_reads.clear()

    result = exporter.sync_spaces(("ENG",))

    assert result.unchanged == 2
    assert result.updated == 0
    assert client.revision_reads == []
    assert client.attachment_reads == [1, 2]
    assert client.download_reads == []


def test_repeated_unchanged_sync_keeps_attachments_reusable(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))

    exporter.sync_spaces(("ENG",))
    client.revision_reads.clear()
    client.download_reads.clear()

    result = exporter.sync_spaces(("ENG",))

    assert result.unchanged == 2
    assert result.updated == 0
    assert client.revision_reads == []
    assert client.download_reads == []
    manifest = json.loads((output / "gitee-wiki-lock.json").read_text(encoding="utf-8"))
    assert manifest["spaces"]["ENG"]["pages"]["2"]["attachments"][0]["id"] == 99


def test_attachment_changes_sync_without_a_page_revision_change(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.revision_reads.clear()
    client.download_reads.clear()
    client.attachments[2] = (Attachment(100, "new.png", "demo/2/new.png", size=3),)

    result = exporter.sync_spaces(("ENG",))

    assert result.updated == 1
    assert client.revision_reads == [2]
    assert client.download_reads == ["demo/2/new.png"]
    assert not (output / "Engineering/Home/Runbook/99.png").exists()
    assert (output / "Engineering/Home/Runbook/100.png").read_bytes() == b"png"


def test_attachment_upload_timestamp_triggers_download_when_other_metadata_is_stable(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.attachments[2] = (
        Attachment(
            99,
            "diagram.png",
            "demo/2/diagram.png",
            size=3,
            updated_at="2026-01-02T03:04:05Z",
        ),
    )
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.download_reads.clear()
    client.attachments[2] = (
        Attachment(
            99,
            "diagram.png",
            "demo/2/diagram.png",
            size=3,
            updated_at="2026-01-03T03:04:05Z",
        ),
    )

    result = exporter.sync_spaces(("ENG",))

    assert result.updated == 1
    assert client.download_reads == ["demo/2/diagram.png"]


def test_changed_attachment_redownloads_only_that_attachment(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.attachments[2] = (
        Attachment(98, "stable.png", "demo/2/stable.png", size=3),
        Attachment(99, "diagram.png", "demo/2/diagram.png", size=3),
    )
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.download_reads.clear()
    client.attachments[2] = (
        Attachment(98, "stable.png", "demo/2/stable.png", size=3),
        Attachment(99, "diagram.png", "demo/2/replaced.png", size=4),
    )

    exporter.sync_spaces(("ENG",))

    assert client.download_reads == ["demo/2/replaced.png"]
    assert (output / "Engineering/Home/Runbook-2.md").read_text(encoding="utf-8") == (
        "# Runbook\n\n![diagram](Runbook/99.png)\n"
    )


def test_changed_revision_downloads_only_the_changed_page(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.revision_reads.clear()
    client.attachment_reads.clear()
    client.revisions[2] = 21
    client.bodies[2] = "Updated"

    result = exporter.sync_spaces(("ENG",))

    assert result.updated == 1
    assert result.unchanged == 1
    assert client.revision_reads == [2]
    assert client.attachment_reads == [1, 2]
    assert (
        (output / "Engineering/Home/Runbook-2.md").read_text(encoding="utf-8").endswith("Updated\n")
    )


def test_page_tree_move_reuses_content_and_rewrites_local_attachment_link(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    client.revision_reads.clear()
    client.attachment_reads.clear()
    client.tree = (
        TreeNode(
            1,
            "Guides",
            "root",
            (TreeNode(2, "Operations", 1),),
        ),
    )

    result = exporter.sync_spaces(("ENG",))

    assert result.moved == 2
    assert client.revision_reads == [1, 2]
    assert client.attachment_reads == [1, 2]
    moved_page = output / "Engineering/Guides/Operations-2.md"
    assert moved_page.read_text(encoding="utf-8") == (
        "# Operations\n\n![diagram](Operations/99.png)\n"
    )
    assert (output / "Engineering/Guides/Operations/99.png").read_bytes() == b"png"
    assert not (output / "Engineering/Home/Runbook-2.md").exists()


def test_page_with_descendants_selects_only_the_requested_subtree(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.tree = (*client.tree, TreeNode(3, "Unrelated", "root"))
    client.revisions[3] = 30
    client.bodies[3] = "Do not export"

    result = WikiExporter(client=client, settings=settings(tmp_path / "mirror")).sync_pages(
        "ENG", (1,), descendants=True
    )

    assert [page.page_id for page in result.pages] == [1, 2]
    assert client.revision_reads == [1, 2]


def test_known_page_id_can_export_when_root_tree_does_not_include_it(tmp_path: Path) -> None:
    client = FakeWikiClient()
    client.tree = ()
    client.direct_pages[4] = PageCandidate(
        page_id=4,
        title="Nested",
        parent_id=3,
        ancestors=("Guides",),
        ancestor_ids=(3,),
    )
    client.revisions[4] = 40
    client.bodies[4] = "Direct page"
    client.attachments[4] = ()

    result = WikiExporter(client=client, settings=settings(tmp_path / "mirror")).sync_pages(
        "ENG", (4,)
    )

    assert result.updated == 1
    assert (tmp_path / "mirror/Engineering/Guides/Nested-4.md").read_text(
        encoding="utf-8"
    ) == "# Nested\n\nDirect page\n"


def test_complete_space_sync_removes_only_manifest_managed_stale_files(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    unmanaged = output / "notes.txt"
    unmanaged.write_text("keep", encoding="utf-8")
    client.tree = (TreeNode(1, "Home", "root"),)
    client.revisions.pop(2)

    result = exporter.sync_spaces(("ENG",))

    assert result.deleted == 1
    assert not (output / "Engineering/Home/Runbook-2.md").exists()
    assert unmanaged.read_text(encoding="utf-8") == "keep"


def test_failed_run_preserves_previous_complete_mirror(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=settings(output))
    exporter.sync_spaces(("ENG",))
    old_manifest = (output / "gitee-wiki-lock.json").read_bytes()
    old_page = (output / "Engineering/Home/Runbook-2.md").read_bytes()
    client.revisions[2] = 21
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        exporter.sync_spaces(("ENG",))

    assert (output / "gitee-wiki-lock.json").read_bytes() == old_manifest
    assert (output / "Engineering/Home/Runbook-2.md").read_bytes() == old_page


def test_interrupted_first_space_sync_reuses_completed_pages_and_cleans_checkpoint(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    client.attachments[1] = (Attachment(88, "welcome.txt", "demo/1/welcome.txt", size=3),)
    client.tenant_id = "example-tenant"
    output = tmp_path / "mirror"
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert not output.exists()
    assert list(tmp_path.glob(".mirror.checkpoint*"))
    assert "example-tenant" not in (tmp_path / ".mirror.checkpoint.json").read_text(
        encoding="utf-8"
    )
    client.fail_page = None
    client.revision_reads.clear()
    client.download_reads.clear()

    result = WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert result.unchanged == 1
    assert result.updated == 1
    assert client.revision_reads == [2]
    assert client.download_reads == ["demo/2/diagram.png"]
    assert not list(tmp_path.glob(".mirror.checkpoint*"))
    assert "_checkpoint" not in (output / "gitee-wiki-lock.json").read_text(encoding="utf-8")


def test_interrupted_page_reuses_checkpointed_attachment(tmp_path: Path) -> None:
    class CrashingRenderer:
        def render(self, _xml: str) -> tuple[str, ...]:
            raise RuntimeError("simulated process interruption")

    client = FakeWikiClient()
    client.tree = (TreeNode(1, "Home", "root"),)
    client.bodies[1] = diagram_body()
    client.attachments[1] = (Attachment(88, "welcome.txt", "demo/1/welcome.txt", size=3),)
    output = tmp_path / "mirror"

    with pytest.raises(ExportError, match="simulated process interruption"):
        WikiExporter(
            client=client,
            settings=settings(output),
            diagram_renderer=CrashingRenderer(),
        ).sync_spaces(("ENG",))

    client.download_reads.clear()
    renderer = FakeDiagramRenderer()

    result = WikiExporter(
        client=client,
        settings=settings(output),
        diagram_renderer=renderer,
    ).sync_spaces(("ENG",))

    assert result.status == "ok"
    assert client.download_reads == []
    assert len(renderer.calls) == 1


def test_interrupted_page_reuses_checkpointed_diagram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitee_wiki_markdown_exporter.exporter as exporter_module

    client = FakeWikiClient()
    client.tree = (TreeNode(1, "Home", "root"),)
    client.bodies[1] = diagram_body()
    output = tmp_path / "mirror"
    renderer = FakeDiagramRenderer()
    original_render = exporter_module.render_wiki_content

    def crash_after_resources(*_args, **_kwargs) -> str:
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(exporter_module, "render_wiki_content", crash_after_resources)
    with pytest.raises(ExportError, match="simulated process interruption"):
        WikiExporter(
            client=client,
            settings=settings(output),
            diagram_renderer=renderer,
        ).sync_spaces(("ENG",))

    monkeypatch.setattr(exporter_module, "render_wiki_content", original_render)

    result = WikiExporter(
        client=client,
        settings=settings(output),
        diagram_renderer=renderer,
    ).sync_spaces(("ENG",))

    assert result.status == "ok"
    assert len(renderer.calls) == 1


def test_corrupt_first_sync_checkpoint_is_discarded(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    sidecar = next(tmp_path.glob(".mirror.checkpoint.json"))
    sidecar.write_text("not json", encoding="utf-8")
    client.fail_page = None
    client.revision_reads.clear()

    WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert client.revision_reads == [1, 2]


def test_changed_export_settings_discard_first_sync_checkpoint(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    client.fail_page = None
    client.revision_reads.clear()
    changed_settings = ExportSettings(output_path=output, include_document_title=False)

    WikiExporter(client=client, settings=changed_settings).sync_spaces(("ENG",))

    assert client.revision_reads == [1, 2]
    assert (output / "Engineering/Home-1.md").read_text(encoding="utf-8") == "Welcome\n"


def test_changed_exporter_version_discards_first_sync_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitee_wiki_markdown_exporter.exporter as exporter_module

    client = FakeWikiClient()
    output = tmp_path / "mirror"
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    client.fail_page = None
    client.revision_reads.clear()
    monkeypatch.setattr(exporter_module, "__version__", "next-version")

    WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert client.revision_reads == [1, 2]


def test_changed_complete_space_target_discards_first_sync_checkpoint(tmp_path: Path) -> None:
    class TargetChangingClient(FakeWikiClient):
        def __init__(self) -> None:
            super().__init__()
            self.fail_space = "DOCS"

        def get_space(self, space_key: str) -> Space:
            if space_key == self.fail_space:
                raise RuntimeError("simulated remote failure")
            return super().get_space(space_key)

    client = TargetChangingClient()
    client.tree = (TreeNode(1, "Home", "root"),)
    output = tmp_path / "mirror"

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG", "DOCS"))

    client.fail_space = ""
    client.revision_reads.clear()

    WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert client.revision_reads == [1]


def test_existing_unmanaged_output_does_not_create_a_resumable_checkpoint(
    tmp_path: Path,
) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    output.mkdir()
    unmanaged = output / "notes.txt"
    unmanaged.write_text("keep", encoding="utf-8")
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert unmanaged.read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob(".mirror.checkpoint*"))


def test_missing_checkpoint_file_forces_a_fresh_first_sync(tmp_path: Path) -> None:
    client = FakeWikiClient()
    output = tmp_path / "mirror"
    client.fail_page = 2

    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    checkpoint = tmp_path / ".mirror.checkpoint"
    (checkpoint / "Engineering/Home-1.md").unlink()
    client.fail_page = None
    client.revision_reads.clear()

    WikiExporter(client=client, settings=settings(output)).sync_spaces(("ENG",))

    assert client.revision_reads == [1, 2]


def test_concurrent_sync_for_same_output_fails_before_remote_work(tmp_path: Path) -> None:
    class BlockingClient(FakeWikiClient):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def get_space(self, space_key: str) -> Space:
            self.entered.set()
            assert self.release.wait(timeout=5)
            return super().get_space(space_key)

    client = BlockingClient()
    client.tree = (TreeNode(1, "Home", "root"),)
    output = tmp_path / "mirror"
    first = WikiExporter(client=client, settings=settings(output))
    second = WikiExporter(client=client, settings=settings(output))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.sync_spaces, ("ENG",))
        assert client.entered.wait(timeout=5)
        with pytest.raises(ExportError, match="already synchronizing"):
            second.sync_spaces(("ENG",))
        client.release.set()
        pending.result(timeout=5)


def test_backup_cleanup_failure_does_not_turn_a_committed_swap_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "mirror"
    output.mkdir()
    (output / "value.txt").write_text("old", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "value.txt").write_text("new", encoding="utf-8")

    def fail_cleanup(_path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)

    _replace_directory(staging=staging, output=output)

    assert (output / "value.txt").read_text(encoding="utf-8") == "new"
