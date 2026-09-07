"""Synthetic regressions for image recovery; never contact a live Wiki."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from test_exporter import FakeWikiClient

from gitee_wiki_markdown_exporter.client import GiteeWikiClient, GiteeWikiError
from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter
from gitee_wiki_markdown_exporter.models import Attachment

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.mark.parametrize(
    "body",
    [
        b"<html>sign in secret-body</html>",
        b"SELECT secret_body FROM example;",
        b"PK\x03\x04not an image",
        b"%PDF-1.5",
        b"\x89PNG\r\n\x1a\n",
    ],
)
def test_download_rejects_false_image_without_leaking_body_or_query(body: bytes) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=body, headers={"content-type": "image/png"})
    )
    with GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="token",
        http_client=httpx.Client(transport=transport),
    ) as client:
        with pytest.raises(GiteeWikiError, match="invalid_image_payload") as caught:
            client.download_attachment("demo/image.png?signature=secret-query", max_bytes=1024)
    assert "secret" not in str(caught.value)


class ImageClient(FakeWikiClient):
    def download_attachment(self, url: str, *, max_bytes: int):
        super().download_attachment(url, max_bytes=max_bytes)
        return PNG, "image/png"


def test_malformed_external_link_does_not_abort_other_resources(tmp_path: Path) -> None:
    client = ImageClient()
    broken = "https://external.example／bad?signature=private"
    client.bodies[2] += f"\n[external]({broken})"
    result = WikiExporter(
        client=client, settings=ExportSettings(output_path=tmp_path / "mirror")
    ).sync_pages("ENG", (2,))
    assert result.status == "ok"
    assert broken in (result.output_path / result.pages[0].path).read_text()
    assert len(client.download_reads) == 1


def test_local_attachment_destination_round_trips_special_filename(tmp_path: Path) -> None:
    client = ImageClient()
    client.attachments[2] = (Attachment(99, "a # (x).png", "demo/2/diagram.png"),)
    result = WikiExporter(
        client=client,
        settings=ExportSettings(
            output_path=tmp_path / "mirror", attachment_path="{page_parent_path}/{attachment_name}"
        ),
    ).sync_pages("ENG", (2,))
    page = result.output_path / result.pages[0].path
    body = page.read_text()
    destination = body.split("![diagram](", 1)[1].split(")", 1)[0]
    assert "%20" in destination and "%23" in destination and "%28" in destination
    assert (page.parent / unquote(destination)).read_bytes() == PNG


def test_old_false_image_is_redownloaded_even_with_matching_manifest_hash(tmp_path: Path) -> None:
    client = ImageClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_pages("ENG", (2,))
    # Find the configured manifest without depending on its default filename.
    manifest_path = output / exporter.settings.lockfile_name
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["spaces"]["ENG"]["pages"]["2"]["attachments"][0]
    bad = b"<html>sign in</html>"
    (output / entry["path"]).write_bytes(bad)
    entry.update(size=len(bad), sha256=hashlib.sha256(bad).hexdigest())
    manifest_path.write_text(json.dumps(manifest))
    client.download_reads.clear()
    exporter.sync_pages("ENG", (2,))
    assert client.download_reads == ["demo/2/diagram.png"]
    assert (output / entry["path"]).read_bytes() == PNG


def test_retry_reuses_download_and_preserves_new_local_files(tmp_path: Path) -> None:
    client = ImageClient()
    output = tmp_path / "mirror"
    output.mkdir()
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    client.fail_page = 1
    with pytest.raises(ExportError, match="simulated remote failure"):
        exporter.sync_pages("ENG", (2, 1))
    assert not list(output.iterdir())
    (output / "local-notes.txt").write_text("created between attempts")
    client.fail_page = None
    client.download_reads.clear()
    exporter.sync_pages("ENG", (2, 1))
    assert not client.download_reads
    assert (output / "local-notes.txt").read_text() == "created between attempts"


@pytest.mark.parametrize("change", ["metadata", "corrupt", "revision", "scope"])
def test_retry_does_not_trust_stale_or_corrupt_resource_cache(tmp_path: Path, change: str) -> None:
    client = ImageClient()
    client.attachments[2] = ()
    client.bodies[2] = "![embedded](/wiki-static/demo/2/a.png)"
    output = tmp_path / "mirror"
    output.mkdir()
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    if change == "metadata":
        client.attachments[2] = (Attachment(99, "a.png", "demo/2/a.png", updated_at="v1"),)
    client.fail_page = 1
    with pytest.raises(ExportError):
        exporter.sync_pages("ENG", (2, 1))
    if change == "metadata":
        client.attachments[2] = (Attachment(99, "a.png", "demo/2/a.png", updated_at="v2"),)
    elif change == "revision":
        client.revisions[2] += 1
    elif change == "corrupt":
        for path in tmp_path.glob(".mirror.resources/**/*.bin"):
            path.write_bytes(b"corrupt")
    else:
        client.tenant_id = "another-tenant"
    client.fail_page = None
    client.download_reads.clear()
    exporter.sync_pages("ENG", (2, 1))
    assert len(client.download_reads) == 1


def test_invalid_listed_url_is_partial_and_other_pages_finish(tmp_path: Path) -> None:
    client = ImageClient()
    client.attachments[2] += (Attachment(100, "bad.png", "https://broken.example／?secret=x"),)
    result = WikiExporter(
        client=client, settings=ExportSettings(output_path=tmp_path / "mirror")
    ).sync_spaces(("ENG",))
    assert result.status == "partial" and len(result.pages) == 2
    assert len(client.download_reads) == 1
    assert "invalid_attachment_url" in result.errors[0]
    assert "secret" not in result.errors[0]


def test_progress_distinguishes_staging_failure_and_commit(tmp_path: Path) -> None:
    client = ImageClient()
    events = []
    output = tmp_path / "mirror"
    output.mkdir()
    exporter = WikiExporter(
        client=client, settings=ExportSettings(output_path=output), progress=events.append
    )
    client.fail_page = 1
    with pytest.raises(ExportError):
        exporter.sync_pages("ENG", (2, 1))
    assert events[-1].phase == "failed"
    assert events[-1].pages_staged == 1 and events[-1].downloaded == 1
    assert not list(output.iterdir())
    events.clear()
    client.fail_page = None
    exporter.sync_pages("ENG", (2, 1))
    assert events[-1].phase == "committed"
    assert events[-1].pages_staged == 2 and events[-1].recovered == 1


@pytest.mark.parametrize(
    "name, content_type, body",
    [
        ("image.png", "application/octet-stream", PNG),
        (
            "drawing.svg",
            "image/svg+xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
            b'<html xmlns="http://www.w3.org/1999/xhtml"><p>text</p></html></foreignObject></svg>',
        ),
        ("query.sql", "text/plain", b"SELECT 1;"),
    ],
)
def test_real_images_and_non_image_attachments_remain_downloadable(
    name, content_type, body
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=body, headers={"content-type": content_type})
    )
    with GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="token",
        http_client=httpx.Client(transport=transport),
    ) as client:
        downloaded, _ = client.download_attachment(f"demo/{name}", max_bytes=1024)
    assert downloaded == body


def test_bad_image_stays_remote_and_next_sync_retries(tmp_path: Path) -> None:
    class BrokenImageClient(ImageClient):
        broken = True

        def download_attachment(self, url, *, max_bytes):
            content, mime = super().download_attachment(url, max_bytes=max_bytes)
            return (b"SELECT secret_body;" if self.broken else content), mime

    client = BrokenImageClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    first = exporter.sync_pages("ENG", (2,))
    assert first.status == "partial" and "invalid_image_payload" in first.errors[0]
    assert "secret_body" not in str(first.to_dict())
    manifest = json.loads((output / exporter.settings.lockfile_name).read_text())
    assert manifest["spaces"]["ENG"]["pages"]["2"]["attachments"] == []
    assert "https://gitee.example.com/wiki-static/" in (output / first.pages[0].path).read_text()
    assert not list(output.rglob("*.png"))
    client.broken = False
    assert exporter.sync_pages("ENG", (2,)).status == "ok"
    assert len(client.download_reads) == 2


def test_keyboard_interrupt_retains_resources_but_removes_disposable_staging(
    tmp_path: Path,
) -> None:
    class InterruptedClient(ImageClient):
        def get_revision(self, space_id, page_id, revision_id):
            if page_id == 1 and self.fail_page == 1:
                raise KeyboardInterrupt()
            return super().get_revision(space_id, page_id, revision_id)

    client = InterruptedClient()
    client.fail_page = 1
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    with pytest.raises(KeyboardInterrupt):
        exporter.sync_pages("ENG", (2, 1))
    assert not output.exists()
    assert not list(tmp_path.glob(".mirror.staging-*"))
    client.fail_page = None
    client.download_reads.clear()
    exporter.sync_pages("ENG", (2, 1))
    assert not client.download_reads
    assert not (tmp_path / ".mirror.resources").exists()


def test_wrong_origin_cannot_reuse_existing_attachment(tmp_path: Path) -> None:
    client = ImageClient()
    output = tmp_path / "mirror"
    exporter = WikiExporter(client=client, settings=ExportSettings(output_path=output))
    exporter.sync_pages("ENG", (2,))
    client.attachments[2] = (
        Attachment(
            99, "diagram.png", "https://external.example/wiki-static/demo/2/diagram.png", size=3
        ),
    )
    client.bodies[2] = "![external](https://external.example/wiki-static/demo/2/diagram.png)"
    result = exporter.sync_pages("ENG", (2,))
    assert result.status == "partial"
    assert "outside" in result.errors[0]


def test_declared_image_is_checked_even_if_transport_claims_plain_text(tmp_path: Path) -> None:
    class WrongMimeClient(ImageClient):
        def download_attachment(self, url, *, max_bytes):
            super().download_attachment(url, max_bytes=max_bytes)
            return b"<html>login</html>", "text/plain"

    client = WrongMimeClient()
    client.attachments[2] = (Attachment(99, "opaque", "demo/2/opaque", content_type="image/png"),)
    client.bodies[2] = "![image](/wiki-static/demo/2/opaque)"
    result = WikiExporter(
        client=client, settings=ExportSettings(output_path=tmp_path / "mirror")
    ).sync_pages("ENG", (2,))
    assert result.status == "partial" and "invalid_image_payload" in result.errors[0]


def test_progress_reports_metadata_checks_before_materializing_pages(tmp_path: Path) -> None:
    client = ImageClient()
    events = []
    WikiExporter(
        client=client,
        settings=ExportSettings(output_path=tmp_path / "mirror"),
        progress=events.append,
    ).sync_spaces(("ENG",))
    checks = [event for event in events if event.phase == "checking"]
    assert [event.pages_checked for event in checks] == [1, 2]
    assert all(event.pages_staged == 0 for event in checks)


@pytest.mark.parametrize(
    "url",
    [
        "https://gitee.example.com:private-port/wiki-static/a.png?signature=private-query",
        "https://broken.example／?signature=private-query",
    ],
)
def test_malformed_download_url_has_sanitized_error_without_a_request(url: str) -> None:
    def handler(_request):
        pytest.fail("invalid URLs must not trigger a request")

    with GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        with pytest.raises(GiteeWikiError, match="invalid_attachment_url") as caught:
            client.download_attachment(url, max_bytes=1024)
    assert "private" not in str(caught.value)
