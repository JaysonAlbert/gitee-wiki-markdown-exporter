"""Typed client for the observed Gitee Project Wiki read contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

import httpx

from gitee_wiki_markdown_exporter.models import Attachment, PageRevision, Space, TreeNode


class GiteeWikiError(RuntimeError):
    """A sanitized transport or response-contract failure."""


class GiteeWikiClient:
    """Read spaces, revisions, and attachments through typed contracts."""

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        token: str,
        timeout: float = 30,
        verify_ssl: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not all((base_url.strip(), tenant_id.strip(), token)):
            raise ValueError("base_url, tenant_id, and token are required")
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id.strip()
        self._token = token
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=False,
        )

    def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GiteeWikiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get_space(self, space_key: str) -> Space:
        """Resolve a space key."""
        data = self._data("GET", f"/api/wiki/spaces/key/{quote(space_key, safe='')}", label="space")
        space_id = _required_int(data.get("id"), "space.id")
        key = _required_text(data.get("key"), "space.key")
        return Space(id=space_id, key=key, name=_text(data.get("name")) or key)

    def get_tree(self, space_id: int) -> tuple[TreeNode, ...]:
        """Read the complete nested page tree for one space."""
        data = self._data("GET", f"/api/wiki/spaces/{space_id}/tree", label="tree")
        values = data.get("tree")
        if not isinstance(values, list):
            raise GiteeWikiError("Wiki tree.data.tree is not a list")
        return tuple(_parse_tree_node(value) for value in values)

    def latest_revision(self, space_id: int, page_id: int) -> int:
        """Return the latest text revision ID for a page."""
        data = self._data(
            "GET",
            f"/api/wiki/spaces/{space_id}/pages/{page_id}/history",
            label="history",
            params={"limit": 1, "offset": 1, "contentType": "text"},
        )
        values = data.get("items") if isinstance(data.get("items"), list) else data.get("list")
        if not isinstance(values, list) or not values or not isinstance(values[0], Mapping):
            raise GiteeWikiError(f"Wiki page {page_id} has no text revision")
        return _required_int(values[0].get("id"), "history.id")

    def get_revision(self, space_id: int, page_id: int, revision_id: int) -> PageRevision:
        """Read one immutable page revision."""
        data = self._data(
            "GET",
            f"/api/wiki/spaces/{space_id}/pages/{page_id}/history/{revision_id}",
            label="history revision",
        )
        content = data.get("content")
        if not isinstance(content, str):
            raise GiteeWikiError(f"Wiki page {page_id} revision {revision_id} is not text")
        return PageRevision(
            id=_int(data.get("id")) or revision_id,
            title=_text(data.get("title")) or str(page_id),
            content_type=_text(data.get("contentType")) or "text",
            content=content,
        )

    def list_attachments(self, page_id: int) -> tuple[Attachment, ...]:
        """Read all attachment metadata for a page."""
        page = 1
        page_size = 100
        attachments: list[Attachment] = []
        while True:
            data = self._data(
                "POST",
                "/api/wiki/attachments/list",
                label="attachment list",
                json_body={
                    "type": 1,
                    "rawId": page_id,
                    "keyword": "",
                    "pageIndex": page,
                    "pageSize": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
            values = data.get("list")
            if not isinstance(values, list):
                raise GiteeWikiError("Wiki attachment list.data.list is not a list")
            for value in values:
                if not isinstance(value, Mapping):
                    raise GiteeWikiError("Wiki attachment list contains a non-object")
                attachment_id = _required_int(
                    value.get("id") or value.get("attachId"), "attachment.id"
                )
                attachments.append(
                    Attachment(
                        id=attachment_id,
                        name=_text(value.get("name") or value.get("filename"))
                        or str(attachment_id),
                        url=_required_text(value.get("url"), "attachment.url"),
                        size=_int(value.get("size")),
                        content_type=_text(value.get("type") or value.get("contentType")),
                    )
                )
            total = _int(data.get("total")) or len(attachments)
            if len(attachments) >= total or not values:
                return tuple(attachments)
            page += 1

    def download_attachment(self, url: str, *, max_bytes: int) -> tuple[bytes, str | None]:
        """Download one attachment while enforcing host and size boundaries."""
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        path = url.lstrip("/")
        if not urlparse(url).scheme and not path.startswith("wiki-static/"):
            path = "wiki-static/" + path
        target = urljoin(self.base_url + "/", url if urlparse(url).scheme else path)
        if _origin(target) != _origin(self.base_url):
            raise GiteeWikiError("attachment URL points outside the configured Gitee host")
        try:
            with self._client.stream("GET", target, headers=self._headers()) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise GiteeWikiError(
                            f"attachment_too_large: response exceeds {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type")
        except GiteeWikiError:
            raise
        except httpx.HTTPError as error:
            raise self._http_error("GET", target, error) from error

    def _data(
        self,
        method: str,
        path: str,
        *,
        label: str,
        params: Mapping[str, object] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        url = self.base_url + path
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise self._http_error(method, url, error) from error
        if not isinstance(payload, Mapping):
            raise GiteeWikiError(f"Wiki {label} response is not an object")
        if payload.get("success") is False or payload.get("code") not in (None, 0, "0"):
            raise GiteeWikiError(f"Wiki {label} request was rejected")
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            raise GiteeWikiError(f"Wiki {label}.data is not an object")
        return data

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "X-Wiki-Tenant-Id": self.tenant_id,
            "Api-Gateway-OAuth-Company": self.tenant_id,
            "User-Agent": "gitee-wiki-markdown-exporter/0.1",
        }

    def _http_error(self, method: str, url: str, error: Exception) -> GiteeWikiError:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        suffix = f" HTTP {status}" if status is not None else f" {type(error).__name__}"
        safe_url = _safe_error_url(url).replace(self._token, "***")
        return GiteeWikiError(f"{method} {safe_url} failed:{suffix}")


def _parse_tree_node(value: object) -> TreeNode:
    if not isinstance(value, Mapping):
        raise GiteeWikiError("Wiki tree contains a non-object node")
    page_id = _required_int(value.get("pageId") or value.get("id"), "tree.pageId")
    children = value.get("children", [])
    if not isinstance(children, list):
        raise GiteeWikiError("Wiki tree node children is not a list")
    parent = value.get("parentId")
    return TreeNode(
        page_id=page_id,
        title=_text(value.get("title") or value.get("name")) or str(page_id),
        parent_id=parent if isinstance(parent, (int, str)) else None,
        children=tuple(_parse_tree_node(child) for child in children),
    )


def _required_int(value: object, label: str) -> int:
    result = _int(value)
    if result is None:
        raise GiteeWikiError(f"Wiki response is missing {label}")
    return result


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _required_text(value: object, label: str) -> str:
    result = _text(value)
    if result is None:
        raise GiteeWikiError(f"Wiki response is missing {label}")
    return result


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def _safe_error_url(url: str) -> str:
    """Return a useful URL without userinfo, query credentials, or fragments."""
    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
