"""Default image filtering must preserve current content and incremental mirror safety."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_exporter import FakeWikiClient, diagram_body
from test_resource_diagnostics import rich

from gitee_wiki_markdown_exporter.config import (
    ConfigError,
    ExportSettings,
    load_settings,
    safe_settings_dict,
)
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter
from gitee_wiki_markdown_exporter.models import Attachment, TreeNode


def configured(tmp_path: Path, enabled: object = True):
    config = tmp_path / "app_data.json"
    config.write_text(
        json.dumps(
            {
                "auth": {"gitee": {"url": "https://gitee.example.com", "tenant_id": "demo"}},
                "export": {
                    "output_path": "mirror",
                    "only_referenced_images": enabled,
                    "cleanup_stale": False,
                },
            }
        )
    )
    return load_settings(config)


def client_with_history():
    client = FakeWikiClient()
    client.attachments[2] += (
        Attachment(100, "historical.png", "demo/2/historical.png"),
        Attachment(101, "notes.pdf", "demo/2/notes.pdf", content_type="application/pdf"),
    )
    return client


def page_entry(output: Path, page_id: int = 2):
    return json.loads((output / "gitee-wiki-lock.json").read_text())["spaces"]["ENG"]["pages"][
        str(page_id)
    ]


@pytest.mark.parametrize("source", ["config", "direct"])
def test_missing_image_policy_defaults_to_filtering(tmp_path, source):
    if source == "config":
        settings = configured(tmp_path)
        payload = json.loads(settings.config_path.read_text())
        del payload["export"]["only_referenced_images"]
        settings.config_path.write_text(json.dumps(payload))
        export_settings = load_settings(settings.config_path).export
    else:
        export_settings = ExportSettings(output_path=tmp_path / "mirror")
    client = client_with_history()
    result = WikiExporter(client=client, settings=export_settings).sync_pages("ENG", (2,))
    assert {a["id"] for a in page_entry(result.output_path)["attachments"]} == {99, 101}
    assert "demo/2/historical.png" not in client.download_reads


def test_filter_excludes_historical_images_before_download_but_keeps_other_attachments(tmp_path):
    settings = configured(tmp_path)
    client = client_with_history()
    # A broken historical image must neither be fetched nor make the result partial.
    client.fail_attachment_urls.add("demo/2/historical.png")
    result = WikiExporter(client=client, settings=settings.export).sync_pages("ENG", (2,))

    assert result.status == "ok"
    assert not result.errors
    assert client.download_reads == ["demo/2/diagram.png", "demo/2/notes.pdf"]
    entry = page_entry(result.output_path)
    assert {a["id"] for a in entry["attachments"]} == {99, 101}
    assert not (result.output_path / "Engineering/Home/Runbook/100.png").exists()
    assert "![diagram](Runbook/99.png)" in (result.output_path / entry["path"]).read_text()
    assert result.attachment_reference_counts == {
        "referenced": 1,
        "unreferenced": 2,
        "unknown": 0,
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_policy_round_trips_in_configuration(tmp_path, enabled):
    settings = configured(tmp_path, enabled)
    assert safe_settings_dict(settings)["export"].get("only_referenced_images") is enabled


@pytest.mark.parametrize("invalid", ["true", 1, None, []])
def test_policy_rejects_non_boolean_values(tmp_path, invalid):
    with pytest.raises(ConfigError, match="export.only_referenced_images must be true or false"):
        configured(tmp_path, invalid)


def test_filtered_run_skips_unchanged_bodies_but_rechecks_excluded_metadata(tmp_path):
    client = client_with_history()
    exporter = WikiExporter(client=client, settings=configured(tmp_path).export)
    exporter.sync_pages("ENG", (2,))
    client.revision_reads.clear()
    client.download_reads.clear()
    client.attachment_reads.clear()
    result = exporter.sync_pages("ENG", (2,))
    assert result.unchanged == 1
    assert client.attachment_reads == [2]
    assert client.revision_reads == client.download_reads == []

    # The body already refers to this URL. An excluded attachment can acquire it
    # without a page revision change, so its old usage classification is stale.
    client.attachments[2] = (
        client.attachments[2][0],
        replace(client.attachments[2][1], url="demo/2/diagram.png"),
        client.attachments[2][2],
    )
    result = exporter.sync_pages("ENG", (2,))
    assert result.updated == 1
    assert client.revision_reads == [2]
    assert {a["id"] for a in page_entry(result.output_path)["attachments"]} == {99, 100, 101}


def test_switch_policy_cleans_only_selected_managed_images_and_can_restore_archival(tmp_path):
    client = client_with_history()
    client.attachments[1] = (Attachment(88, "old.png", "demo/1/old.png"),)
    full = WikiExporter(client=client, settings=configured(tmp_path, False).export)
    full.sync_spaces(("ENG",))
    output = full.settings.output_path
    old_image = output / "Engineering/Home/Runbook/100.png"
    other_image = output / page_entry(output, 1)["attachments"][0]["path"]
    notes = old_image.parent / "personal.png"
    notes.write_bytes(b"local file")
    assert old_image.is_file()
    client.download_reads.clear()

    filtered = WikiExporter(client=client, settings=configured(tmp_path).export)
    filtered.sync_pages("ENG", (2,))
    assert not old_image.exists()
    assert other_image.is_file()
    assert notes.read_bytes() == b"local file"
    assert not client.download_reads  # Referenced bytes are reused during migration.

    full.sync_pages("ENG", (2,))
    assert old_image.is_file()
    assert client.download_reads == ["demo/2/historical.png"]


def test_revision_removal_and_readdition_reconcile_images(tmp_path):
    client = FakeWikiClient()
    exporter = WikiExporter(client=client, settings=configured(tmp_path).export)
    first = exporter.sync_pages("ENG", (2,))
    image = first.output_path / page_entry(first.output_path)["attachments"][0]["path"]
    client.bodies[2] = "The image was removed from the current revision."
    client.revisions[2] += 1
    exporter.sync_pages("ENG", (2,))
    assert not image.exists()
    client.bodies[2] = "![current](/wiki-static/demo/2/diagram.png)"
    client.revisions[2] += 1
    exporter.sync_pages("ENG", (2,))
    assert image.is_file()


@pytest.mark.parametrize(
    "body",
    [
        "[download](/wiki-static/demo/2/diagram.png)",
        rich({"type": "image", "attrs": {"src": "/wiki-static/demo/2/diagram.png"}}),
        rich({"type": "attachments", "attrs": {"attachment-checked-list": [99]}}),
        rich({"type": "attachments", "attrs": {}}),
        rich({"type": "futureWidget", "attrs": {"resource": 99}}),
        '<img src="/wiki-static/demo/2/diagram.png">',
        "![current][image]\n\n[image]: /wiki-static/demo/2/diagram.png",
    ],
)
def test_keep_referenced_or_uncertain_images(tmp_path, body):
    client = FakeWikiClient()
    client.bodies[2] = body
    result = WikiExporter(client=client, settings=configured(tmp_path).export).sync_pages(
        "ENG", (2,)
    )
    assert result.status == "ok"
    entry = page_entry(result.output_path)
    assert {a["id"] for a in entry["attachments"]} == {99}
    assert (result.output_path / entry["attachments"][0]["path"]).is_file()


@pytest.mark.parametrize(
    "attachment",
    [
        Attachment(99, "UPPER.PNG", "demo/blob"),
        Attachment(99, "blob", "demo/blob", content_type="image/png"),
        Attachment(99, "blob", "demo/image.png"),
    ],
)
def test_image_detection_uses_name_mime_and_url(tmp_path, attachment):
    client = FakeWikiClient()
    client.attachments[2] = (attachment,)
    client.bodies[2] = "No current image references."
    result = WikiExporter(client=client, settings=configured(tmp_path).export).sync_pages(
        "ENG", (2,)
    )
    assert not client.download_reads
    assert page_entry(result.output_path)["attachments"] == []


def test_filter_keeps_body_only_resources_and_drawio(tmp_path):
    from test_exporter import FakeDiagramRenderer

    client = FakeWikiClient()
    body = json.loads(diagram_body())
    body["default"]["content"].append(
        {"type": "image", "attrs": {"src": "/wiki-static/demo/embedded.png"}}
    )
    client.bodies[2] = json.dumps(body)
    result = WikiExporter(
        client=client, settings=configured(tmp_path).export, diagram_renderer=FakeDiagramRenderer()
    ).sync_pages("ENG", (2,))
    entry = page_entry(result.output_path)
    assert entry["attachments"] == []
    assert len(entry["embeddedResources"]) == 1
    assert len(entry["diagrams"]) == 1
    assert all((result.output_path / p).is_file() for p in entry["diagrams"][0]["paths"])


def test_filter_failure_before_swap_preserves_old_images_and_manifest(tmp_path):
    client = client_with_history()
    output = configured(tmp_path, False).export.output_path
    WikiExporter(client=client, settings=configured(tmp_path, False).export).sync_spaces(("ENG",))
    old_manifest = (output / "gitee-wiki-lock.json").read_bytes()
    image = output / "Engineering/Home/Runbook/100.png"
    old_bytes = image.read_bytes()
    client.fail_page = 1
    with pytest.raises(ExportError, match="simulated remote failure"):
        WikiExporter(client=client, settings=configured(tmp_path).export).sync_pages("ENG", (2, 1))
    assert image.read_bytes() == old_bytes
    assert (output / "gitee-wiki-lock.json").read_bytes() == old_manifest


def test_first_sync_checkpoint_does_not_retain_images_after_policy_change(tmp_path):
    client = client_with_history()
    client.tree = (TreeNode(2, "Runbook"), TreeNode(1, "Home"))
    client.fail_page = 1
    with pytest.raises(ExportError):
        WikiExporter(client=client, settings=configured(tmp_path, False).export).sync_spaces(
            ("ENG",)
        )
    assert not (tmp_path / "mirror").exists()
    client.fail_page = None
    client.download_reads.clear()
    result = WikiExporter(client=client, settings=configured(tmp_path).export).sync_spaces(("ENG",))
    assert result.status == "ok"
    assert not list(result.output_path.rglob("100.png"))
    assert "demo/2/historical.png" not in client.download_reads


def test_filtered_page_move_keeps_incremental_state_and_local_image_links(tmp_path):
    client = client_with_history()
    exporter = WikiExporter(client=client, settings=configured(tmp_path).export)
    exporter.sync_pages("ENG", (2,))
    client.tree = (TreeNode(1, "Home", "root", (TreeNode(2, "Renamed", 1),)),)
    client.download_reads.clear()
    result = exporter.sync_pages("ENG", (2,))
    entry = page_entry(result.output_path)
    body = (result.output_path / entry["path"]).read_text()
    assert "![diagram](Renamed/99.png)" in body
    assert not list(result.output_path.rglob("100.png"))
    assert not client.download_reads
    client.revision_reads.clear()
    assert exporter.sync_pages("ENG", (2,)).unchanged == 1
    assert not client.revision_reads


@pytest.mark.parametrize(
    "body, content_status",
    [
        ("![current](/wiki-static/demo/2/diagram.png)", "partial"),
        (rich({"type": "futureWidget"}), "unknown"),
    ],
)
def test_filter_does_not_hide_failures_for_retained_images(tmp_path, body, content_status):
    client = FakeWikiClient()
    client.bodies[2] = body
    client.fail_attachment_urls.add("demo/2/diagram.png")
    result = WikiExporter(client=client, settings=configured(tmp_path).export).sync_pages(
        "ENG", (2,)
    )
    assert result.status == "partial"
    assert result.content_status == content_status
    assert len(result.resource_issues) == 1


def test_filter_reports_malformed_attachment_url_without_aborting_the_export(tmp_path):
    client = FakeWikiClient()
    client.attachments[2] = (Attachment(99, "image.png", "https://[broken"),)
    client.bodies[2] = "No image references."
    exporter = WikiExporter(client=client, settings=configured(tmp_path).export)
    result = exporter.sync_pages("ENG", (2,))
    assert result.status == "partial"
    assert not client.download_reads
    assert len(result.resource_issues) == 1
    assert exporter.sync_pages("ENG", (2,)).status == "partial"
