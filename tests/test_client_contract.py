import json

import httpx

from gitee_wiki_markdown_exporter.client import GiteeWikiClient, GiteeWikiError


def test_client_uses_observed_paths_headers_and_envelopes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/spaces/key/ENG"):
            return httpx.Response(200, json={"code": 0, "data": {"id": 34, "key": "ENG"}})
        if path.endswith("/spaces/34/tree"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "tree": [
                            {
                                "id": 1,
                                "pageId": 1,
                                "title": "Root",
                                "children": [{"pageId": 2, "title": "Child"}],
                            }
                        ]
                    },
                },
            )
        if path.endswith("/spaces/34/pages/2"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": 2,
                        "title": "Child",
                        "parent": 1,
                        "pagePathList": [
                            {"id": 34, "title": "Documents", "contentType": "text"},
                            {"id": 1, "title": "Root", "contentType": "text"},
                            {"id": 2, "title": "Child", "contentType": "text"},
                        ],
                    }
                },
            )
        if path.endswith("/pages/2/history"):
            assert dict(request.url.params) == {
                "limit": "1",
                "offset": "1",
                "contentType": "text",
            }
            return httpx.Response(200, json={"data": {"total": 1, "items": [{"id": 7}]}})
        if path.endswith("/pages/2/history/7"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": 7,
                        "title": "Child",
                        "contentType": "text",
                        "content": "Body",
                    }
                },
            )
        if path.endswith("/spaces/ENG/pages/501/component"):
            return httpx.Response(
                200,
                json={"data": {"id": 501, "content": '<mxfile><diagram id="a"/></mxfile>'}},
            )
        if path.endswith("/attachments/list"):
            assert json.loads(request.content)["rawId"] == 2
            return httpx.Response(
                200,
                json={
                    "data": {
                        "total": 1,
                        "list": [
                            {
                                "id": 9,
                                "name": "image.png",
                                "url": "demo/2/image.png",
                                "uploadAt": "2026-01-02T03:04:05Z",
                            }
                        ],
                    }
                },
            )
        if path.endswith("/wiki-static/demo/2/image.png"):
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="top-secret",
        http_client=http_client,
    )

    space = client.get_space("ENG")
    tree = client.get_tree(space.id)
    wiki_page = client.get_page(space.id, 2)
    revision = client.latest_revision(space.id, 2)
    page = client.get_revision(space.id, 2, revision)
    diagram = client.get_diagram_component(space.key, 501)
    attachments = client.list_attachments(2)
    content, content_type = client.download_attachment(attachments[0].url, max_bytes=10)

    assert tree[0].children[0].page_id == 2
    assert wiki_page.page_id == 2
    assert wiki_page.ancestors == ("Root",)
    assert wiki_page.ancestor_ids == (1,)
    assert page.content == "Body"
    assert diagram.id == 501
    assert diagram.content == '<mxfile><diagram id="a"/></mxfile>'
    assert content == b"png"
    assert content_type == "image/png"
    assert attachments[0].updated_at == "2026-01-02T03:04:05Z"
    assert all(request.headers["authorization"] == "Bearer top-secret" for request in requests)
    assert all(request.headers["x-wiki-tenant-id"] == "demo" for request in requests)


def test_client_rejects_cross_host_attachments_without_leaking_token() -> None:
    client = GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="top-secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500))
        ),
    )

    try:
        client.download_attachment("https://evil.example/image.png", max_bytes=10)
    except GiteeWikiError as error:
        assert "outside" in str(error)
        assert "top-secret" not in str(error)
    else:
        raise AssertionError("cross-host attachment was accepted")


def test_client_expands_lazy_non_leaf_tree_nodes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/spaces/34/tree") and request.url.params.get("parent") == "1":
            return httpx.Response(
                200,
                json={"data": {"tree": [{"id": 2, "title": "Child", "parent": 1, "isLeaf": True}]}},
            )
        if request.url.path.endswith("/spaces/34/tree"):
            return httpx.Response(
                200,
                json={"data": {"tree": [{"id": 1, "title": "Root", "isLeaf": False}]}},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="top-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    tree = client.get_tree(34)

    assert tree[0].children[0].page_id == 2
    assert tree[0].children[0].parent_id == 1


def test_client_bounds_attachment_before_returning_content() -> None:
    client = GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="top-secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"too-large")
            )
        ),
    )

    try:
        client.download_attachment("demo/large.bin", max_bytes=3)
    except GiteeWikiError as error:
        assert "attachment_too_large" in str(error)
    else:
        raise AssertionError("oversized attachment was accepted")


def test_attachment_error_redacts_signed_query_credentials() -> None:
    client = GiteeWikiClient(
        base_url="https://gitee.example.com",
        tenant_id="demo",
        token="top-secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(403))
        ),
    )

    try:
        client.download_attachment(
            "https://gitee.example.com/wiki-static/image.png?sig=signed-secret",
            max_bytes=10,
        )
    except GiteeWikiError as error:
        message = str(error)
        assert "HTTP 403" in message
        assert "signed-secret" not in message
        assert "top-secret" not in message
        assert message == "GET https://gitee.example.com/wiki-static/image.png failed: HTTP 403"
    else:
        raise AssertionError("failed attachment download did not raise")
