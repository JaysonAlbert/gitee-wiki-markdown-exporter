import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_exporter import FakeWikiClient

from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter
from gitee_wiki_markdown_exporter.models import Space, TreeNode


class NavigationClient(FakeWikiClient):
    tenant_id = "example"


def test_local_links_keep_labels_fragments_and_refresh_after_target_rename(tmp_path: Path) -> None:
    client = NavigationClient()
    client.bodies[1] = (
        "[Read guide](/wiki/example/space/ENG/doc/2?tracking=unused#install)\n"
        "`[Code](/wiki/example/space/ENG/doc/2)`\n"
        "[Other tenant](/wiki/other/space/ENG/doc/2)\n"
        "[External](https://other.example.com/wiki/example/space/ENG/doc/2)"
    )
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(("ENG",))
    source = output / "Engineering/Home-1.md"
    body = source.read_text()
    assert "[Read guide](Home/Runbook-2.md#install)" in body
    assert "`[Code](/wiki/example/space/ENG/doc/2)`" in body
    assert "[Other tenant](/wiki/other/space/ENG/doc/2)" in body
    assert "[External](https://other.example.com/wiki/example/space/ENG/doc/2)" in body
    client.tree = (TreeNode(1, "Home", "root", (TreeNode(2, "New guide", 1),)),)
    client.revision_reads.clear()
    client.download_reads.clear()
    result = exporter.sync_spaces(("ENG",))
    assert "[Read guide](Home/New%20guide-2.md#install)" in source.read_text()
    assert client.revision_reads == [2]
    assert client.download_reads == []
    assert result.updated + result.moved == 2
    assert exporter.sync_spaces(("ENG",)).unchanged == 2


def test_deleted_target_becomes_remote_and_untracked_files_are_untouched(tmp_path: Path) -> None:
    client = NavigationClient()
    client.bodies[1] = "[Guide](/wiki/example/space/ENG/doc/2#install)"
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(("ENG",))
    unmanaged = output / "personal.md"
    unmanaged.write_text("[Keep](Engineering/Home/Runbook-2.md)")
    client.tree = (TreeNode(1, "Home", "root"),)
    client.revision_reads.clear()
    result = exporter.sync_spaces(("ENG",))
    assert result.deleted == 1
    assert client.revision_reads == []
    assert (
        "[Guide](https://gitee.example.com/wiki/example/space/ENG/doc/2#install)"
        in (output / "Engineering/Home-1.md").read_text()
    )
    assert unmanaged.read_text() == "[Keep](Engineering/Home/Runbook-2.md)"


def test_selected_target_rename_updates_incoming_links_without_reading_unselected_body(
    tmp_path: Path,
) -> None:
    client = NavigationClient()
    client.bodies[1] = "[Guide](/wiki/example/space/ENG/doc/2)"
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(("ENG",))
    client.tree = (TreeNode(1, "Home", "root", (TreeNode(2, "New", 1),)),)
    client.revision_reads.clear()
    exporter.sync_pages("ENG", (2,))
    assert client.revision_reads == [2]
    assert "[Guide](Home/New-2.md)" in (output / "Engineering/Home-1.md").read_text()


@pytest.mark.parametrize("order", [("ENG", "OPS"), ("OPS", "ENG")])
def test_cross_space_links_do_not_depend_on_selection_order(
    tmp_path: Path, order: tuple[str, ...]
) -> None:
    class CrossSpaceClient(NavigationClient):
        def get_space(self, space_key: str) -> Space:
            return Space(34 if space_key == "ENG" else 35, space_key, space_key)

        def get_tree(self, space_id: int) -> tuple[TreeNode, ...]:
            return (
                (TreeNode(1, "Home", "root"),)
                if space_id == 34
                else (TreeNode(2, "Guide", "root"),)
            )

    client = CrossSpaceClient()
    client.bodies[1] = "[Guide](/wiki/example/space/OPS/doc/2)"
    client.bodies[2] = "[Home](/wiki/example/space/ENG/doc/1)"
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(order)
    assert "[Guide](../OPS/Guide-2.md)" in (output / "ENG/Home-1.md").read_text()
    assert "[Home](../ENG/Home-1.md)" in (output / "OPS/Guide-2.md").read_text()
    assert exporter.sync_spaces(order).unchanged == 2


def test_breadcrumbs_and_metadata_use_existing_context_and_settings_refresh(tmp_path: Path) -> None:
    client = NavigationClient()
    output = tmp_path / "mirror"
    settings = ExportSettings(output_path=output, include_yaml_frontmatter=True)
    WikiExporter(client=client, settings=settings).sync_spaces(("ENG",))
    page = output / "Engineering/Home/Runbook-2.md"
    body = page.read_text()
    assert "Engineering / [Home](../Home-1.md) / Runbook" in body
    assert 'gitee_source_url: "https://gitee.example.com/wiki/example/space/ENG/doc/2"' in body
    assert 'gitee_space_name: "Engineering"' in body
    assert "gitee_parent_id: 1" in body
    assert 'gitee_ancestors: ["Home"]' in body
    assert "gitee_ancestor_ids: [1]" in body
    client.download_reads.clear()
    changed = replace(settings, include_page_breadcrumbs=False, include_yaml_frontmatter=False)
    result = WikiExporter(client=client, settings=changed).sync_spaces(("ENG",))
    assert result.updated == 2
    assert "gitee_source_url" not in page.read_text()
    assert "Engineering /" not in page.read_text()
    assert client.download_reads == []


def test_renderer_upgrade_refreshes_color_without_redownloading_attachments(tmp_path: Path) -> None:
    client = NavigationClient()
    client.bodies[2] = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Notice",
                            "marks": [{"type": "textStyle", "attrs": {"color": "#f00"}}],
                        }
                    ],
                }
            ],
        }
    )
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(("ENG",))
    manifest_path = output / "gitee-wiki-lock.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["spaces"]["ENG"]["pages"]["2"]["rendererVersion"] = 6
    manifest_path.write_text(json.dumps(manifest))
    page = output / "Engineering/Home/Runbook-2.md"
    page.write_text("# Runbook\n\nNotice\n")
    client.download_reads.clear()
    assert exporter.sync_spaces(("ENG",)).updated == 1
    assert '<span style="color: #ff0000;">Notice</span>' in page.read_text()
    assert client.download_reads == []
    assert exporter.sync_spaces(("ENG",)).unchanged == 2


def test_source_move_rebases_page_links_without_refetching_its_body(tmp_path: Path) -> None:
    client = NavigationClient()
    client.tree = (*client.tree, TreeNode(3, "Archive", "root", (TreeNode(4, "Nested", 3),)))
    client.revisions.update({3: 30, 4: 40})
    client.bodies.update({2: "[Home](/wiki/example/space/ENG/doc/1)", 3: "Archive", 4: "Nested"})
    client.attachments.update({3: (), 4: ()})
    output = tmp_path / "mirror"
    exporter = WikiExporter(
        client=client, settings=ExportSettings(output_path=output, include_page_breadcrumbs=False)
    )
    exporter.sync_spaces(("ENG",))
    client.tree = (
        TreeNode(1, "Home", "root"),
        TreeNode(3, "Archive", "root", (TreeNode(4, "Nested", 3, (TreeNode(2, "Runbook", 4),)),)),
    )
    client.revision_reads.clear()
    client.download_reads.clear()
    assert exporter.sync_spaces(("ENG",)).moved == 1
    assert (
        "[Home](../../Home-1.md)"
        in (output / "Engineering/Archive/Nested/Runbook-2.md").read_text()
    )
    assert client.revision_reads == []
    assert client.download_reads == []


def test_selected_page_uses_plain_missing_ancestor_and_remote_missing_target(
    tmp_path: Path,
) -> None:
    client = NavigationClient()
    client.bodies[2] = "[Home](/wiki/example/space/ENG/doc/1?unused=value#intro)"
    output = tmp_path / "mirror"
    WikiExporter(client=client, settings=ExportSettings(output_path=output)).sync_pages("ENG", (2,))
    body = (output / "Engineering/Home/Runbook-2.md").read_text()
    assert "Engineering / Home / Runbook" in body
    assert "[Home](https://gitee.example.com/wiki/example/space/ENG/doc/1#intro)" in body


def test_link_repair_failure_preserves_live_mirror_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitee_wiki_markdown_exporter.exporter as exporter_module

    client = NavigationClient()
    client.bodies[1] = "[Guide](/wiki/example/space/ENG/doc/2)"
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_spaces(("ENG",))
    source = output / "Engineering/Home-1.md"
    before = source.read_bytes()
    manifest = (output / "gitee-wiki-lock.json").read_bytes()
    client.tree = (TreeNode(1, "Home", "root", (TreeNode(2, "New", 1),)),)
    write = exporter_module._atomic_write_text

    def fail_repair(path: Path, text: str) -> None:
        if path.name == "Home-1.md" and "Home/New-2.md" in text:
            raise OSError("simulated link repair failure")
        write(path, text)

    monkeypatch.setattr(exporter_module, "_atomic_write_text", fail_repair)
    with pytest.raises(ExportError, match="simulated link repair failure"):
        exporter.sync_spaces(("ENG",))
    assert source.read_bytes() == before
    assert (output / "gitee-wiki-lock.json").read_bytes() == manifest


def test_breadcrumb_labels_escape_markdown_html_and_newlines(tmp_path: Path) -> None:
    client = NavigationClient()
    client.tree = (TreeNode(1, "[Parent] <tag>\nnext", "root", (TreeNode(2, "Runbook", 1),)),)
    output = tmp_path / "mirror"
    WikiExporter(client=client, settings=ExportSettings(output_path=output)).sync_pages("ENG", (2,))
    pages = list(output.rglob("Runbook-2.md"))
    assert len(pages) == 1
    assert r"Engineering / \[Parent\] &lt;tag&gt; next / Runbook" in pages[0].read_text()


@pytest.mark.parametrize("prefix", ["> ", "    ", "- > "])
def test_local_link_repair_preserves_nested_fenced_examples(tmp_path: Path, prefix: str) -> None:
    client = NavigationClient()
    sample = f"{prefix}```markdown\n{prefix}[Example](/wiki/example/space/ENG/doc/2)\n{prefix}```"
    client.bodies[1] = sample + "\n\n[Actual](/wiki/example/space/ENG/doc/2)"
    output = tmp_path / "mirror"
    WikiExporter(client=client, settings=ExportSettings(output_path=output)).sync_spaces(("ENG",))
    body = (output / "Engineering/Home-1.md").read_text()
    assert sample in body
    assert "[Actual](Home/Runbook-2.md)" in body


def test_initial_checkpoint_resume_localizes_reused_pages(tmp_path: Path) -> None:
    client = NavigationClient()
    client.bodies[1] = "[Guide](/wiki/example/space/ENG/doc/2)"
    client.fail_page = 2
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    with pytest.raises(ExportError, match="simulated remote failure"):
        exporter.sync_spaces(("ENG",))
    client.fail_page = None
    client.revision_reads.clear()
    exporter.sync_spaces(("ENG",))
    assert client.revision_reads == [2]
    assert "[Guide](Home/Runbook-2.md)" in (output / "Engineering/Home-1.md").read_text()
