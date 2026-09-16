"""Transport-boundary image compatibility and safe failure categorization."""

import base64

import httpx
import pytest

from gitee_wiki_markdown_exporter.client import GiteeWikiClient, GiteeWikiError

JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAABP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AJ8AwR//2Q=="
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>'
DOCTYPE = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'


def download(data, suffix):
    requests = []

    def serve(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=data, headers={"content-type": "image/" + suffix})

    with GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="token",
        http_client=httpx.Client(transport=httpx.MockTransport(serve)),
    ) as client:
        result = client.download_attachment("demo/image." + suffix, max_bytes=4096)
    assert len(requests) == 1
    return result[0]


@pytest.mark.parametrize("trailer", [b"", b"\0" * 24, b"metadata after image"])
def test_jpeg_container_allows_data_after_end_marker(trailer):
    assert download(JPEG + trailer, "jpeg") == JPEG + trailer


def test_standard_svg_doctype_needs_no_external_request():
    assert download(DOCTYPE + SVG, "svg") == DOCTYPE + SVG


@pytest.mark.parametrize(
    "data, suffix, code",
    [
        (JPEG[:-10], "jpeg", "invalid_image_container"),
        (b"<html>private login body</html>", "png", "html_response"),
        (b"PK\x03\x04not an image", "png", "image_type_mismatch"),
        (b'<!DOCTYPE svg [<!ENTITY x "secret">]><svg>&x;</svg>', "svg", "unsafe_svg"),
        (b'<!DOCTYPE svg SYSTEM "https://external.example.com/a.dtd">' + SVG, "svg", "unsafe_svg"),
        (
            '<!DOCTYPE svg [<!ENTITY x "private">]><svg>&x;</svg>'.encode("utf-16"),
            "svg",
            "unsafe_svg",
        ),
        (b"<svg><path></svg>", "svg", "invalid_image_container"),
    ],
)
def test_rejected_image_has_safe_distinct_reason(data, suffix, code):
    with pytest.raises(GiteeWikiError, match=code) as caught:
        download(data, suffix)
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert "external.example" not in str(caught.value)


def test_end_marker_inside_segment_does_not_rescue_truncated_jpeg():
    # A complete APP segment may contain marker-like bytes; only scan EOI terminates JPEG.
    payload = b"\xff\xd8\xff\xe1\x00\x06\xff\xd9xx" + JPEG[2:-2]
    with pytest.raises(GiteeWikiError, match="invalid_image_container"):
        download(payload, "jpeg")
