"""Revision-aware transactional Markdown export service."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.manifest import load_manifest, write_manifest
from gitee_wiki_markdown_exporter.models import (
    Attachment,
    PageCandidate,
    PageOutcome,
    PageRevision,
    Space,
    SyncResult,
    TreeNode,
)
from gitee_wiki_markdown_exporter.paths import render_attachment_path, render_page_path
from gitee_wiki_markdown_exporter.rich_text import render_wiki_content

_MARKDOWN_RENDERER_VERSION = 3


class WikiReader(Protocol):
    """Read capabilities required by the exporter."""

    base_url: str

    def get_space(self, space_key: str) -> Space: ...

    def get_tree(self, space_id: int) -> tuple[TreeNode, ...]: ...

    def latest_revision(self, space_id: int, page_id: int) -> int: ...

    def get_revision(self, space_id: int, page_id: int, revision_id: int) -> PageRevision: ...

    def list_attachments(self, page_id: int) -> tuple[Attachment, ...]: ...

    def download_attachment(self, url: str, *, max_bytes: int) -> tuple[bytes, str | None]: ...


class ExportError(RuntimeError):
    """Raised when a mirror run cannot complete safely."""


@dataclass(frozen=True)
class Selection:
    """One requested space selection."""

    space_key: str
    page_ids: tuple[int, ...] = ()
    descendants: bool = False
    complete_space: bool = False
    cleanup_stale: bool = False


class WikiExporter:
    """Build and atomically replace a local Wiki mirror."""

    def __init__(self, *, client: WikiReader, settings: ExportSettings) -> None:
        self.client = client
        self.settings = settings

    def sync_spaces(
        self, space_keys: tuple[str, ...], *, cleanup_stale: bool | None = None
    ) -> SyncResult:
        """Synchronize complete spaces."""
        if not space_keys:
            raise ExportError("at least one space key is required")
        cleanup = self.settings.cleanup_stale if cleanup_stale is None else cleanup_stale
        return self._sync(
            tuple(Selection(key, complete_space=True, cleanup_stale=cleanup) for key in space_keys)
        )

    def sync_pages(
        self,
        space_key: str,
        page_ids: tuple[int, ...],
        *,
        descendants: bool = False,
    ) -> SyncResult:
        """Synchronize selected pages, optionally including descendants."""
        if not page_ids:
            raise ExportError("at least one page ID is required")
        return self._sync((Selection(space_key, page_ids, descendants=descendants),))

    def _sync(self, selections: tuple[Selection, ...]) -> SyncResult:
        output = self.settings.output_path
        output.parent.mkdir(parents=True, exist_ok=True)
        previous = load_manifest(output / self.settings.lockfile_name)
        next_manifest = copy.deepcopy(previous)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
        outcomes: list[PageOutcome] = []
        deleted = 0
        try:
            if output.exists():
                shutil.copytree(output, staging, dirs_exist_ok=True, copy_function=_link_or_copy)
            for selection in selections:
                selection_outcomes, selection_deleted = self._sync_selection(
                    staging=staging,
                    previous=previous,
                    next_manifest=next_manifest,
                    selection=selection,
                )
                outcomes.extend(selection_outcomes)
                deleted += selection_deleted
            write_manifest(staging / self.settings.lockfile_name, next_manifest)
            _prune_empty_directories(staging)
            _replace_directory(staging=staging, output=output)
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            if isinstance(error, ExportError):
                raise
            raise ExportError(str(error)) from error

        return SyncResult(
            status="ok",
            output_path=output,
            updated=sum(outcome.status == "updated" for outcome in outcomes),
            unchanged=sum(outcome.status == "unchanged" for outcome in outcomes),
            moved=sum(outcome.status == "moved" for outcome in outcomes),
            deleted=deleted,
            pages=tuple(outcomes),
        )

    def _sync_selection(
        self,
        *,
        staging: Path,
        previous: dict[str, Any],
        next_manifest: dict[str, Any],
        selection: Selection,
    ) -> tuple[list[PageOutcome], int]:
        space = self.client.get_space(selection.space_key)
        tree = self.client.get_tree(space.id)
        all_candidates = _flatten_tree(tree)
        candidates = _select_candidates(all_candidates, selection)
        missing = set(selection.page_ids) - {candidate.page_id for candidate in candidates}
        if missing:
            values = ", ".join(str(value) for value in sorted(missing))
            raise ExportError(f"pages not found in space {space.key}: {values}")

        previous_spaces = previous.get("spaces", {})
        previous_space = (
            previous_spaces.get(space.key, {}) if isinstance(previous_spaces, dict) else {}
        )
        previous_pages = previous_space.get("pages", {}) if isinstance(previous_space, dict) else {}
        if not isinstance(previous_pages, dict):
            previous_pages = {}

        next_spaces = next_manifest.setdefault("spaces", {})
        next_space = copy.deepcopy(previous_space) if isinstance(previous_space, dict) else {}
        next_space.update({"id": space.id, "key": space.key, "name": space.name})
        next_pages = next_space.setdefault("pages", {})
        if not isinstance(next_pages, dict):
            next_pages = {}
            next_space["pages"] = next_pages

        outcomes: list[PageOutcome] = []
        selected_ids: set[str] = set()
        for candidate in candidates:
            page_key = str(candidate.page_id)
            selected_ids.add(page_key)
            old_entry = previous_pages.get(page_key, {})
            if not isinstance(old_entry, dict):
                old_entry = {}
            revision = self.client.latest_revision(space.id, candidate.page_id)
            desired_path = render_page_path(
                self.settings.page_path,
                space_name=space.name,
                ancestors=candidate.ancestors,
                page_title=candidate.title,
                page_id=candidate.page_id,
            )
            status, entry = self._sync_page(
                staging=staging,
                space=space,
                candidate=candidate,
                revision=revision,
                desired_path=desired_path,
                old_entry=old_entry,
            )
            next_pages[page_key] = entry
            outcomes.append(
                PageOutcome(
                    page_id=candidate.page_id,
                    status=status,
                    path=desired_path,
                    revision=str(revision),
                )
            )

        deleted = 0
        if selection.complete_space and selection.cleanup_stale:
            for page_key in set(previous_pages) - selected_ids:
                old_entry = previous_pages.get(page_key)
                if isinstance(old_entry, dict):
                    _remove_managed_entry(staging, old_entry)
                next_pages.pop(page_key, None)
                deleted += 1
        next_spaces[space.key] = next_space
        return outcomes, deleted

    def _sync_page(
        self,
        *,
        staging: Path,
        space: Space,
        candidate: PageCandidate,
        revision: int,
        desired_path: Path,
        old_entry: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        old_path = _manifest_path(old_entry.get("path"))
        old_attachments = old_entry.get("attachments", [])
        old_revision = str(old_entry.get("revision", ""))
        attachments = self.client.list_attachments(candidate.page_id)
        attachments_unchanged = _attachments_match(
            staging=staging,
            old_attachments=old_attachments,
            current_attachments=attachments,
        )
        unchanged = (
            self.settings.skip_unchanged
            and old_revision == str(revision)
            and old_entry.get("rendererVersion") == _MARKDOWN_RENDERER_VERSION
            and old_entry.get("title") == candidate.title
            and old_path is not None
            and (staging / old_path).is_file()
            and attachments_unchanged
        )
        if unchanged and old_path == desired_path:
            entry = copy.deepcopy(old_entry)
            entry.update(_page_metadata(candidate, revision, desired_path))
            return "unchanged", entry
        if unchanged and old_path is not None:
            entry = self._move_unchanged_page(
                staging=staging,
                candidate=candidate,
                revision=revision,
                old_path=old_path,
                desired_path=desired_path,
                old_attachments=old_attachments,
            )
            return "moved", entry

        page = self.client.get_revision(space.id, candidate.page_id, revision)
        attachment_entries: list[dict[str, object]] = []
        replacements: dict[str, str] = {}
        old_by_id = _attachment_entries_by_id(old_attachments)
        for attachment in attachments:
            attachment_path = render_attachment_path(
                self.settings.attachment_path,
                page_path=desired_path,
                page_title=candidate.title,
                attachment_id=attachment.id,
                attachment_name=attachment.name,
            )
            old_attachment = old_by_id.get(attachment.id)
            old_attachment_path = (
                _manifest_path(old_attachment.get("path")) if old_attachment else None
            )
            if (
                old_attachment is not None
                and old_attachment_path is not None
                and (staging / old_attachment_path).is_file()
                and _attachment_metadata_matches(old_attachment, attachment)
            ):
                _copy_managed_file(staging, old_attachment_path, attachment_path)
                attachment_entry = copy.deepcopy(old_attachment)
                attachment_entry.update(_attachment_metadata(attachment, attachment_path))
            else:
                content, content_type = self.client.download_attachment(
                    attachment.url, max_bytes=self.settings.max_attachment_bytes
                )
                _atomic_write_bytes(staging / attachment_path, content)
                attachment_entry = _attachment_metadata(attachment, attachment_path)
                attachment_entry.update(
                    {
                        "size": len(content),
                        "contentType": content_type or attachment.content_type,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            relative_link = _relative_link(desired_path.parent, attachment_path)
            for source in _attachment_source_variants(self.client.base_url, attachment.url):
                replacements[source] = relative_link
            if old_attachment is not None and isinstance(old_attachment.get("urlPath"), str):
                for source in _attachment_source_variants(
                    self.client.base_url, str(old_attachment["urlPath"])
                ):
                    replacements[source] = relative_link
            attachment_entries.append(attachment_entry)

        body = _rewrite_links(render_wiki_content(page.content), replacements)
        document = _render_document(
            body,
            title=candidate.title,
            space=space,
            page_id=candidate.page_id,
            revision=revision,
            include_title=self.settings.include_document_title,
            include_frontmatter=self.settings.include_yaml_frontmatter,
        )
        if old_path is not None and old_path != desired_path:
            _remove_path(staging, old_path)
        for old_attachment in old_attachments if isinstance(old_attachments, list) else []:
            if isinstance(old_attachment, dict):
                old_attachment_path = _manifest_path(old_attachment.get("path"))
                if old_attachment_path and old_attachment_path not in {
                    Path(str(entry["path"])) for entry in attachment_entries
                }:
                    _remove_path(staging, old_attachment_path)
        _atomic_write_text(staging / desired_path, document)
        entry = _page_metadata(candidate, revision, desired_path)
        entry["attachments"] = attachment_entries
        moved = (
            old_revision == str(revision)
            and old_path is not None
            and old_path != desired_path
            and attachments_unchanged
        )
        return ("moved" if moved else "updated"), entry

    def _move_unchanged_page(
        self,
        *,
        staging: Path,
        candidate: PageCandidate,
        revision: int,
        old_path: Path,
        desired_path: Path,
        old_attachments: object,
    ) -> dict[str, Any]:
        old_document = (staging / old_path).read_text(encoding="utf-8")
        attachment_entries: list[dict[str, object]] = []
        replacements: dict[str, str] = {}
        if isinstance(old_attachments, list):
            for item in old_attachments:
                if not isinstance(item, dict):
                    continue
                old_attachment_path = _manifest_path(item.get("path"))
                if old_attachment_path is None:
                    continue
                attachment_id = int(item["id"])
                name = str(item.get("name") or attachment_id)
                new_attachment_path = render_attachment_path(
                    self.settings.attachment_path,
                    page_path=desired_path,
                    page_title=candidate.title,
                    attachment_id=attachment_id,
                    attachment_name=name,
                )
                old_link = _relative_link(old_path.parent, old_attachment_path)
                new_link = _relative_link(desired_path.parent, new_attachment_path)
                replacements[old_link] = new_link
                _move_file(staging, old_attachment_path, new_attachment_path)
                copied = copy.deepcopy(item)
                copied["path"] = new_attachment_path.as_posix()
                attachment_entries.append(copied)
        _move_file(
            staging,
            old_path,
            desired_path,
            content=_rewrite_links(old_document, replacements),
        )
        entry = _page_metadata(candidate, revision, desired_path)
        entry["attachments"] = attachment_entries
        return entry


def _flatten_tree(nodes: tuple[TreeNode, ...]) -> tuple[PageCandidate, ...]:
    result: list[PageCandidate] = []

    def visit(node: TreeNode, ancestors: tuple[str, ...], ancestor_ids: tuple[int, ...]) -> None:
        result.append(
            PageCandidate(
                page_id=node.page_id,
                title=node.title,
                parent_id=node.parent_id,
                ancestors=ancestors,
                ancestor_ids=ancestor_ids,
            )
        )
        for child in node.children:
            visit(child, (*ancestors, node.title), (*ancestor_ids, node.page_id))

    for root in nodes:
        visit(root, (), ())
    return tuple(result)


def _select_candidates(
    candidates: tuple[PageCandidate, ...], selection: Selection
) -> tuple[PageCandidate, ...]:
    if selection.complete_space:
        return candidates
    roots = set(selection.page_ids)
    if selection.descendants:
        return tuple(
            candidate
            for candidate in candidates
            if candidate.page_id in roots or roots.intersection(candidate.ancestor_ids)
        )
    return tuple(candidate for candidate in candidates if candidate.page_id in roots)


def _page_metadata(candidate: PageCandidate, revision: int, desired_path: Path) -> dict[str, Any]:
    return {
        "pageId": candidate.page_id,
        "title": candidate.title,
        "parentId": candidate.parent_id,
        "revision": str(revision),
        "rendererVersion": _MARKDOWN_RENDERER_VERSION,
        "path": desired_path.as_posix(),
        "attachments": [],
    }


def _render_document(
    body: str,
    *,
    title: str,
    space: Space,
    page_id: int,
    revision: int,
    include_title: bool,
    include_frontmatter: bool,
) -> str:
    pieces: list[str] = []
    if include_frontmatter:
        pieces.append(
            "---\n"
            f"gitee_page_id: {page_id}\n"
            f"gitee_revision: {revision}\n"
            f"gitee_space: {json.dumps(space.key, ensure_ascii=False)}\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            "---"
        )
    stripped = body.lstrip()
    if include_title and not stripped.startswith(f"# {title}\n"):
        pieces.append(f"# {title}")
    pieces.append(body.rstrip())
    return "\n\n".join(piece for piece in pieces if piece) + "\n"


def _attachment_source_variants(base_url: str, url: str) -> set[str]:
    relative = _attachment_url_path(url)
    source_path = urlparse(url).path
    return {url, source_path, relative, base_url.rstrip("/") + relative} - {""}


def _attachment_url_path(url: str) -> str:
    relative = urlparse(url).path
    if not relative.startswith("/wiki-static/"):
        relative = "/wiki-static/" + relative.lstrip("/")
    return relative


def _rewrite_links(content: str, replacements: dict[str, str]) -> str:
    for source in sorted(replacements, key=len, reverse=True):
        if "?" in source or "#" in source:
            content = content.replace(source, replacements[source])
            continue
        suffix = r"(?:\?[^)\s<>\"']*)?(?:#[^)\s<>\"']*)?"
        content = re.sub(
            re.escape(source) + suffix,
            lambda _match, replacement=replacements[source]: replacement,
            content,
        )
    return content


def _relative_link(page_parent: Path, attachment_path: Path) -> str:
    start = page_parent.as_posix() if page_parent.as_posix() != "." else "."
    return posixpath.relpath(attachment_path.as_posix(), start=start)


def _manifest_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ExportError("manifest contains an unsafe managed path")
    return path


def _attachments_exist(staging: Path, value: object) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(item, dict)
        and (path := _manifest_path(item.get("path"))) is not None
        and (staging / path).is_file()
        for item in value
    )


def _attachment_entries_by_id(value: object) -> dict[int, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            result[int(item.get("id"))] = item
        except (TypeError, ValueError):
            continue
    return result


def _attachment_metadata(attachment: Attachment, attachment_path: Path) -> dict[str, object]:
    return {
        "id": attachment.id,
        "name": attachment.name,
        "path": attachment_path.as_posix(),
        "urlPath": _attachment_url_path(attachment.url),
        "remoteSize": attachment.size,
        "remoteContentType": attachment.content_type,
    }


def _attachment_metadata_matches(entry: dict[str, Any], attachment: Attachment) -> bool:
    try:
        entry_id = int(entry.get("id"))
    except (TypeError, ValueError):
        return False
    return (
        entry_id == attachment.id
        and entry.get("name") == attachment.name
        and entry.get("urlPath") == _attachment_url_path(attachment.url)
        and entry.get("remoteSize") == attachment.size
        and entry.get("remoteContentType") == attachment.content_type
    )


def _attachments_match(
    *,
    staging: Path,
    old_attachments: object,
    current_attachments: tuple[Attachment, ...],
) -> bool:
    old_by_id = _attachment_entries_by_id(old_attachments)
    if len(old_by_id) != len(current_attachments):
        return False
    return all(
        (entry := old_by_id.get(attachment.id)) is not None
        and _attachment_metadata_matches(entry, attachment)
        and (path := _manifest_path(entry.get("path"))) is not None
        and (staging / path).is_file()
        for attachment in current_attachments
    )


def _remove_managed_entry(staging: Path, entry: dict[str, Any]) -> None:
    page_path = _manifest_path(entry.get("path"))
    if page_path:
        _remove_path(staging, page_path)
    attachments = entry.get("attachments", [])
    if isinstance(attachments, list):
        for attachment in attachments:
            if isinstance(attachment, dict):
                attachment_path = _manifest_path(attachment.get("path"))
                if attachment_path:
                    _remove_path(staging, attachment_path)


def _remove_path(root: Path, relative: Path) -> None:
    target = root / relative
    if target.is_file() or target.is_symlink():
        target.unlink()


def _move_file(root: Path, old: Path, new: Path, *, content: str | None = None) -> None:
    source = root / old
    if not source.is_file():
        raise ExportError(f"managed file is missing: {old.as_posix()}")
    target = root / new
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        source.replace(target)
    else:
        _atomic_write_text(target, content)
        if source != target:
            source.unlink()


def _copy_managed_file(root: Path, old: Path, new: Path) -> None:
    if old == new:
        return
    source = root / old
    if not source.is_file():
        raise ExportError(f"managed file is missing: {old.as_posix()}")
    target = root / new
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    _link_or_copy(str(source), str(target))


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{uuid4().hex}")
    temporary.write_bytes(content)
    temporary.replace(path)


def _link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
        return destination
    except OSError:
        return shutil.copy2(source, destination)


def _replace_directory(*, staging: Path, output: Path) -> None:
    if not output.exists():
        staging.replace(output)
        return
    backup = output.parent / f".{output.name}.backup-{uuid4().hex}"
    output.replace(backup)
    try:
        staging.replace(output)
    except Exception:
        backup.replace(output)
        raise
    else:
        try:
            shutil.rmtree(backup)
        except OSError:
            pass


def _prune_empty_directories(root: Path) -> None:
    for directory, _children, _files in os.walk(root, topdown=False):
        path = Path(directory)
        if path != root:
            try:
                path.rmdir()
            except OSError:
                pass
