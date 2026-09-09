"""Read-only requests recover from transient failures without relaxing download safety."""

import json
from collections.abc import Iterator

import httpx
import pytest

from gitee_wiki_markdown_exporter.client import GiteeWikiClient, GiteeWikiError


@pytest.mark.parametrize("operation", ["space", "attachments", "download"])
def test_transient_requests_retry_and_honor_bounded_retry_after(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(429, headers={"Retry-After": "999"})
        if operation == "space":
            return httpx.Response(200, json={"data": {"id": 1, "key": "DOCS"}})
        if operation == "attachments":
            assert request.method == "POST"
            assert json.loads(request.content)["rawId"] == 2
            return httpx.Response(200, json={"data": {"list": []}})
        return httpx.Response(200, content=b"complete", headers={"content-type": "text/plain"})

    with GiteeWikiClient(
        base_url="https://example.com",
        tenant_id="example",
        token="fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        if operation == "space":
            assert client.get_space("DOCS").id == 1
        elif operation == "attachments":
            assert client.list_attachments(2) == ()
        else:
            assert (
                client.download_attachment("/wiki-static/file.txt", max_bytes=20)[0] == b"complete"
            )
    assert len(requests) == 3
    assert waits == [30, 30]
    assert all(request.content == requests[0].content for request in requests)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retry_exhaustion_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="private response fake-token")

    client = GiteeWikiClient(
        base_url="https://example.com",
        tenant_id="example",
        token="fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(GiteeWikiError) as caught:
        client.download_attachment("/wiki-static/file.txt?token=fake-token#private", max_bytes=20)
    assert (
        str(caught.value) == f"GET https://example.com/wiki-static/file.txt failed: HTTP {status}"
    )
    assert calls == 4
    assert waits == [0.5, 1, 2]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(404),
        httpx.Response(302),
        httpx.Response(200, text="not JSON"),
        httpx.Response(200, json={"success": False}),
        httpx.Response(200, json={"data": {}}),
    ],
)
def test_permanent_errors_do_not_retry(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    client = GiteeWikiClient(
        base_url="https://example.com",
        tenant_id="example",
        token="fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(GiteeWikiError):
        client.get_space("DOCS")
    assert calls == 1
    assert waits == []


def test_interrupted_stream_is_closed_and_restarted_from_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    calls = 0
    closed: list[bool] = []

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"partial"
            raise httpx.ReadError("private transport failure")

        def close(self) -> None:
            closed.append(True)

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=InterruptedStream())
        assert closed == [True]
        return httpx.Response(200, content=b"complete")

    client = GiteeWikiClient(
        base_url="https://example.com",
        tenant_id="example",
        token="fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert client.download_attachment("file.txt", max_bytes=8)[0] == b"complete"
    assert calls == 2
    assert waits == [0.5]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("3", 3),
        ("invalid", 0.5),
        ("-1", 0.5),
        ("NaN", 0.5),
        ("Thu, 01 Jan 1970 00:16:45 GMT", 5),
        ("Thu, 01 Jan 1970 00:00:00 GMT", 0),
    ],
)
def test_retry_after_seconds_dates_and_invalid_values(
    monkeypatch: pytest.MonkeyPatch, header: str, expected: float
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    monkeypatch.setattr("time.time", lambda: 1000)
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": header})
        return httpx.Response(200, json={"data": {"id": 1, "key": "DOCS"}})

    client = GiteeWikiClient(
        base_url="https://example.com",
        tenant_id="example",
        token="fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert client.get_space("DOCS").key == "DOCS"
    assert waits == [expected]
