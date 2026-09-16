"""Current-content completeness is distinct from full attachment archival."""

import json
from pathlib import Path

import pytest
from test_exporter import FakeWikiClient

from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.exporter import WikiExporter


def rich(*nodes):
    return json.dumps({"default": {"type": "doc", "content": list(nodes)}})


@pytest.mark.parametrize(
    ("body", "reference", "content_status"),
    [
        ("![a](/wiki-static/demo/2/diagram.png)", "referenced", "partial"),
        ("[a](demo/2/diagram.png)", "referenced", "partial"),
        ("[a](/demo/2/diagram.png)", "referenced", "partial"),
        ("No inline resources", "unreferenced", "complete"),
        (rich({"type": "futureWidget", "attrs": {"resource": 99}}), "unknown", "unknown"),
        (
            rich({"type": "image", "attrs": {"id": "99", "src": "/other.png"}}),
            "unreferenced",
            "complete",
        ),
        (
            rich({"type": "attachments", "attrs": {"attachment-checked-list": [99]}}),
            "referenced",
            "partial",
        ),
        (rich({"type": "attachments", "attrs": {}}), "referenced", "partial"),
        (
            rich({"type": "attachments", "attrs": {"attachment-checked-list": "100"}}),
            "unreferenced",
            "complete",
        ),
        ("[a][image]\n\n[image]: /wiki-static/demo/2/diagram.png", "unknown", "unknown"),
        ("{malformed rich text", "unknown", "unknown"),
        ("`![example](/wiki-static/demo/2/diagram.png)`", "unreferenced", "complete"),
        (
            "[a](https://external.example.com/wiki-static/demo/2/diagram.png)",
            "unreferenced",
            "complete",
        ),
        (
            "[a](https://gitee.example.com/wiki-static/demo/2/%64iagram.png?token=secret#x)",
            "referenced",
            "partial",
        ),
    ],
)
def test_failed_attachment_reports_current_content_impact(
    tmp_path, body, reference, content_status
):
    client = FakeWikiClient()
    client.bodies[2] = body
    client.fail_attachment_urls.add("demo/2/diagram.png")
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=tmp_path / "mirror"))
    result = exporter.sync_pages("ENG", (2,))
    payload = result.to_dict()
    assert result.status == "partial"  # Archival still attempts even unused attachments.
    assert payload.get("contentStatus") == content_status
    issues = payload.get("resourceIssues", [])
    assert len(issues) == 1
    assert issues[0] == {
        "pageId": 2,
        "resourceKind": "attachment",
        "resourceId": 99,
        "reference": reference,
        "code": "http_error",
        "retryable": True,
        "httpStatus": 500,
    }
    assert "secret" not in json.dumps(issues)
    manifest = json.loads((result.output_path / exporter.settings.lockfile_name).read_text())
    assert manifest["spaces"]["ENG"]["pages"]["2"].get("attachmentReferences") == {"99": reference}


def test_reference_state_reused_and_updated_with_revision(tmp_path: Path):
    client = FakeWikiClient()
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=tmp_path / "mirror"))
    exporter.sync_pages("ENG", (2,))
    client.revision_reads.clear()
    unchanged = exporter.sync_pages("ENG", (2,)).to_dict()
    assert client.revision_reads == []
    assert unchanged.get("attachmentReferenceCounts") == {
        "referenced": 1,
        "unreferenced": 0,
        "unknown": 0,
    }
    client.revisions[2] += 1
    client.bodies[2] = "Removed inline image"
    changed = exporter.sync_pages("ENG", (2,)).to_dict()
    assert changed.get("attachmentReferenceCounts") == {
        "referenced": 0,
        "unreferenced": 1,
        "unknown": 0,
    }
    assert changed.get("contentStatus") == "complete"


@pytest.mark.parametrize(
    "message, code, retryable",
    [
        ("invalid_image_payload: html_response", "html_response", False),
        ("invalid_image_payload: image_type_mismatch", "image_type_mismatch", False),
        ("invalid_image_payload: invalid_image_container", "invalid_image_container", False),
        ("attachment_too_large: limit exceeded", "size_limit", False),
        ("ReadTimeout", "network_error", True),
    ],
)
def test_download_failure_codes_do_not_copy_remote_values(tmp_path, message, code, retryable):
    from gitee_wiki_markdown_exporter.client import GiteeWikiError

    client = FakeWikiClient()

    def failed_download(*args, **kwargs):
        raise GiteeWikiError(message + " https://example.com/?token=secret")

    client.download_attachment = failed_download
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=tmp_path / "mirror"))
    payload = exporter.sync_pages("ENG", (2,)).to_dict()
    issue = payload["resourceIssues"][0]
    assert issue["code"] == code
    assert issue["retryable"] is retryable
    assert issue["reference"] == "referenced"
    assert "secret" not in json.dumps(issue)
    if code == "size_limit":
        assert issue["limitBytes"] == exporter.settings.max_attachment_bytes


def test_legacy_manifest_refreshes_reference_state_once(tmp_path):
    client = FakeWikiClient()
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=tmp_path / "mirror"))
    result = exporter.sync_pages("ENG", (2,))
    lock = result.output_path / exporter.settings.lockfile_name
    manifest = json.loads(lock.read_text())
    entry = manifest["spaces"]["ENG"]["pages"]["2"]
    del entry["attachmentReferences"]
    del entry["attachmentReferenceVersion"]
    lock.write_text(json.dumps(manifest))
    client.revision_reads.clear()
    exporter.sync_pages("ENG", (2,))
    assert len(client.revision_reads) == 1
    client.revision_reads.clear()
    exporter.sync_pages("ENG", (2,))
    assert client.revision_reads == []
