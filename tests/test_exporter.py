import json
import shutil
from pathlib import Path

import pytest

from gitee_wiki_markdown_exporter.client import GiteeWikiError
from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter, _replace_directory
from gitee_wiki_markdown_exporter.models import Attachment, PageRevision, Space, TreeNode


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

    def get_space(self, space_key: str) -> Space:
        return Space(34, space_key, "Engineering")

    def get_tree(self, _space_id: int) -> tuple[TreeNode, ...]:
        return self.tree

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
