"""Revision-aware transactional Markdown export service."""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from gitee_wiki_markdown_exporter import __version__
from gitee_wiki_markdown_exporter.client import GiteeWikiError
from gitee_wiki_markdown_exporter.config import ExportSettings
from gitee_wiki_markdown_exporter.diagram import (
    ChromeDiagramRenderer,
    DiagramRenderer,
    DiagramRenderError,
)
from gitee_wiki_markdown_exporter.manifest import (
    ManifestError,
    empty_manifest,
    load_manifest,
    validate_manifest,
    write_manifest,
)
from gitee_wiki_markdown_exporter.models import (
    Attachment,
    DiagramComponent,
    PageCandidate,
    PageOutcome,
    PageRevision,
    Space,
    SyncResult,
    TreeNode,
)
from gitee_wiki_markdown_exporter.paths import (
    render_attachment_path,
    render_diagram_path,
    render_page_path,
)
from gitee_wiki_markdown_exporter.rich_text import find_diagram_references, render_wiki_content

_MARKDOWN_RENDERER_VERSION = 4
_CHECKPOINT_SCHEMA_VERSION = 1
_CHECKPOINT_PARTIAL = "_checkpointPartial"
_OUTPUT_LOCK_GUARD = threading.Lock()
_OUTPUT_LOCKS: set[Path] = set()


class WikiReader(Protocol):
    """Read capabilities required by the exporter."""

    base_url: str

    def get_space(self, space_key: str) -> Space: ...

    def get_tree(self, space_id: int) -> tuple[TreeNode, ...]: ...

    def get_page(self, space_id: int, page_id: int) -> PageCandidate: ...

    def latest_revision(self, space_id: int, page_id: int) -> int: ...

    def get_revision(self, space_id: int, page_id: int, revision_id: int) -> PageRevision: ...

    def list_attachments(self, page_id: int) -> tuple[Attachment, ...]: ...

    def download_attachment(self, url: str, *, max_bytes: int) -> tuple[bytes, str | None]: ...

    def get_diagram_component(self, space_key: str, component_page_id: int) -> DiagramComponent: ...


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


@dataclass(frozen=True)
class _PageRemoteState:
    revision: int
    attachments: tuple[Attachment, ...]


@dataclass(frozen=True)
class _Checkpoint:
    staging: Path
    sidecar: Path
    state: Path
    fingerprint: str

    def initialize(self) -> None:
        payload = {
            "schemaVersion": _CHECKPOINT_SCHEMA_VERSION,
            "provider": "gitee-project-wiki",
            "fingerprint": self.fingerprint,
        }
        temporary = self.state / "header.json"
        _atomic_write_text(
            temporary,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        temporary.replace(self.sidecar)

    def persist_space(self, space_key: str, space: dict[str, Any]) -> None:
        payload = {"kind": "space", "space": _checkpoint_space_metadata(space)}
        _atomic_write_text(
            self.state / _checkpoint_state_name("space", space_key),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def persist_page(
        self,
        space_key: str,
        space: dict[str, Any],
        page_key: str,
        entry: dict[str, Any],
    ) -> None:
        payload = {
            "kind": "page",
            "space": _checkpoint_space_metadata(space),
            "pageKey": page_key,
            "entry": entry,
        }
        _atomic_write_text(
            self.state / _checkpoint_state_name("page", space_key, page_key),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def delete_page(self, space_key: str, page_key: str) -> None:
        (self.state / _checkpoint_state_name("page", space_key, page_key)).unlink(missing_ok=True)


def _checkpoint_paths(output: Path) -> tuple[Path, Path, Path]:
    return (
        output.parent / f".{output.name}.checkpoint",
        output.parent / f".{output.name}.checkpoint.json",
        output.parent / f".{output.name}.checkpoint-state",
    )


def _checkpoint_state_name(kind: str, *identity: str) -> str:
    digest = hashlib.sha256("\0".join(identity).encode("utf-8")).hexdigest()
    return f"{kind}-{digest}.json"


def _checkpoint_space_metadata(space: dict[str, Any]) -> dict[str, Any]:
    return {key: space[key] for key in ("id", "key", "name")}


def _checkpoint_fingerprint(
    *,
    output: Path,
    settings: ExportSettings,
    client: WikiReader,
    selections: tuple[Selection, ...],
) -> str:
    provider_identity = hashlib.sha256(
        (client.base_url.rstrip("/") + "\0" + str(getattr(client, "tenant_id", ""))).encode("utf-8")
    ).hexdigest()
    payload = {
        "checkpointSchemaVersion": _CHECKPOINT_SCHEMA_VERSION,
        "applicationVersion": __version__,
        "rendererVersion": _MARKDOWN_RENDERER_VERSION,
        "providerIdentity": provider_identity,
        "output": str(output.resolve()),
        "settings": {
            "pagePath": settings.page_path,
            "attachmentPath": settings.attachment_path,
            "diagramPath": settings.diagram_path,
            "includeDocumentTitle": settings.include_document_title,
            "includeYamlFrontmatter": settings.include_yaml_frontmatter,
            "skipUnchanged": settings.skip_unchanged,
            "cleanupStale": settings.cleanup_stale,
            "lockfileName": settings.lockfile_name,
            "maxAttachmentBytes": settings.max_attachment_bytes,
        },
        "selections": [
            {
                "spaceKey": selection.space_key,
                "pageIds": list(selection.page_ids),
                "descendants": selection.descendants,
                "completeSpace": selection.complete_space,
                "cleanupStale": selection.cleanup_stale,
            }
            for selection in selections
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_checkpoint(
    *,
    output: Path,
    settings: ExportSettings,
    client: WikiReader,
    selections: tuple[Selection, ...],
) -> tuple[_Checkpoint, dict[str, Any], bool]:
    staging, sidecar, state = _checkpoint_paths(output)
    fingerprint = _checkpoint_fingerprint(
        output=output,
        settings=settings,
        client=client,
        selections=selections,
    )
    checkpoint = _Checkpoint(
        staging=staging,
        sidecar=sidecar,
        state=state,
        fingerprint=fingerprint,
    )
    resumed = _load_checkpoint(checkpoint)
    if resumed is not None:
        return checkpoint, resumed, True

    _discard_checkpoint(staging, sidecar, state)
    staging.mkdir()
    state.mkdir()
    if output.exists():
        shutil.copytree(output, staging, dirs_exist_ok=True, copy_function=_link_or_copy)
    checkpoint.initialize()
    return checkpoint, empty_manifest(), False


def _load_checkpoint(checkpoint: _Checkpoint) -> dict[str, Any] | None:
    if not any(
        path.exists() for path in (checkpoint.staging, checkpoint.sidecar, checkpoint.state)
    ):
        return None
    if (
        not checkpoint.staging.is_dir()
        or checkpoint.staging.is_symlink()
        or not checkpoint.sidecar.is_file()
        or checkpoint.sidecar.is_symlink()
        or not checkpoint.state.is_dir()
        or checkpoint.state.is_symlink()
    ):
        return None
    try:
        payload = json.loads(checkpoint.sidecar.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schemaVersion") != _CHECKPOINT_SCHEMA_VERSION
            or payload.get("provider") != "gitee-project-wiki"
            or payload.get("fingerprint") != checkpoint.fingerprint
        ):
            return None
        manifest = _load_checkpoint_state(checkpoint.state)
        validate_manifest(manifest, label="checkpoint")
        if not _checkpoint_files_exist(checkpoint.staging, manifest):
            return None
        return manifest
    except (OSError, json.JSONDecodeError, ManifestError, ExportError):
        return None


def _load_checkpoint_state(state: Path) -> dict[str, Any]:
    manifest = empty_manifest()
    spaces = manifest["spaces"]
    for path in sorted(state.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("kind") not in {"space", "page"}:
            raise ManifestError("invalid checkpoint state record")
        space = payload.get("space")
        if not isinstance(space, dict) or set(space) != {"id", "key", "name"}:
            raise ManifestError("invalid checkpoint space state")
        space_key = space.get("key")
        if not isinstance(space_key, str) or not space_key:
            raise ManifestError("invalid checkpoint space key")
        kind = str(payload["kind"])
        page_key = payload.get("pageKey")
        identity = (space_key,) if kind == "space" else (space_key, str(page_key))
        if path.name != _checkpoint_state_name(kind, *identity):
            raise ManifestError("checkpoint state identity mismatch")
        existing = spaces.setdefault(space_key, {**space, "pages": {}})
        if not isinstance(existing, dict) or _checkpoint_space_metadata(existing) != space:
            raise ManifestError("conflicting checkpoint space state")
        if kind == "page":
            entry = payload.get("entry")
            if not isinstance(page_key, str) or not page_key or not isinstance(entry, dict):
                raise ManifestError("invalid checkpoint page state")
            existing["pages"][page_key] = entry
    return manifest


def _checkpoint_files_exist(staging: Path, manifest: dict[str, Any]) -> bool:
    spaces = manifest.get("spaces")
    if not isinstance(spaces, dict):
        return False
    for space in spaces.values():
        if not isinstance(space, dict) or not isinstance(space.get("pages"), dict):
            return False
        for entry in space["pages"].values():
            if not isinstance(entry, dict):
                return False
            page_path = _manifest_path(entry.get("path"))
            if page_path is None:
                return False
            if entry.get(_CHECKPOINT_PARTIAL) is not True and not (staging / page_path).is_file():
                return False
            attachments = entry.get("attachments")
            if not isinstance(attachments, list) or not _attachments_exist(staging, attachments):
                return False
            diagrams = entry.get("diagrams")
            if not isinstance(diagrams, list) or not _diagrams_exist(staging, diagrams):
                return False
    return True


def _discard_checkpoint(staging: Path, sidecar: Path, state: Path) -> None:
    for path in (staging, sidecar, state):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


@contextmanager
def _output_lock(output: Path) -> Iterator[None]:
    lock_path = output.parent / f".{output.name}.sync.lock"
    lock_key = lock_path.resolve()
    with _OUTPUT_LOCK_GUARD:
        if lock_key in _OUTPUT_LOCKS:
            raise ExportError(f"another process is already synchronizing {output}")
        _OUTPUT_LOCKS.add(lock_key)
    try:
        handle = lock_path.open("a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise ExportError(
                        f"another process is already synchronizing {output}"
                    ) from error
                raise
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
    finally:
        with _OUTPUT_LOCK_GUARD:
            _OUTPUT_LOCKS.remove(lock_key)


def _save_checkpoint_page(
    *,
    checkpoint: _Checkpoint,
    space_key: str,
    space: dict[str, Any],
    pages: dict[str, Any],
    page_key: str,
    entry: dict[str, Any],
) -> None:
    pages[page_key] = copy.deepcopy(entry)
    checkpoint.persist_page(space_key, space, page_key, entry)


def _checkpoint_page_entry(
    *,
    candidate: PageCandidate,
    revision: int,
    desired_path: Path,
    attachments: list[dict[str, object]],
    diagrams: list[dict[str, object]],
) -> dict[str, Any]:
    entry = _page_metadata(candidate, revision, desired_path)
    entry["attachments"] = copy.deepcopy(attachments)
    entry["diagrams"] = copy.deepcopy(diagrams)
    entry["diagramsComplete"] = False
    entry[_CHECKPOINT_PARTIAL] = True
    return entry


def _remove_checkpoint_fields(manifest: dict[str, Any]) -> None:
    spaces = manifest.get("spaces")
    if not isinstance(spaces, dict):
        return
    for space in spaces.values():
        if not isinstance(space, dict) or not isinstance(space.get("pages"), dict):
            continue
        for entry in space["pages"].values():
            if isinstance(entry, dict):
                entry.pop(_CHECKPOINT_PARTIAL, None)


class WikiExporter:
    """Build and atomically replace a local Wiki mirror."""

    def __init__(
        self,
        *,
        client: WikiReader,
        settings: ExportSettings,
        diagram_renderer: DiagramRenderer | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.diagram_renderer = diagram_renderer or ChromeDiagramRenderer(base_url=client.base_url)

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
        with _output_lock(output):
            return self._sync_locked(selections)

    def _sync_locked(self, selections: tuple[Selection, ...]) -> SyncResult:
        output = self.settings.output_path
        manifest_path = output / self.settings.lockfile_name
        previous = load_manifest(manifest_path)
        checkpoint: _Checkpoint | None = None
        checkpoint_resumed = False
        if not output.exists() and all(selection.complete_space for selection in selections):
            checkpoint, previous, checkpoint_resumed = _prepare_checkpoint(
                output=output,
                settings=self.settings,
                client=self.client,
                selections=selections,
            )
            staging = checkpoint.staging
            next_manifest = empty_manifest()
        else:
            _discard_checkpoint(*_checkpoint_paths(output))
            next_manifest = copy.deepcopy(previous)
            staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
        outcomes: list[PageOutcome] = []
        errors: list[str] = []
        deleted = 0
        try:
            if output.exists() and checkpoint is None:
                shutil.copytree(output, staging, dirs_exist_ok=True, copy_function=_link_or_copy)
            for selection in selections:
                selection_outcomes, selection_deleted, selection_errors = self._sync_selection(
                    staging=staging,
                    previous=previous,
                    next_manifest=next_manifest,
                    selection=selection,
                    checkpoint=checkpoint,
                    checkpoint_resumed=checkpoint_resumed,
                )
                outcomes.extend(selection_outcomes)
                deleted += selection_deleted
                errors.extend(selection_errors)
            _remove_checkpoint_fields(next_manifest)
            write_manifest(staging / self.settings.lockfile_name, next_manifest)
            _prune_empty_directories(staging)
            _replace_directory(staging=staging, output=output)
        except Exception as error:
            if checkpoint is None:
                shutil.rmtree(staging, ignore_errors=True)
            if isinstance(error, ExportError):
                raise
            raise ExportError(str(error)) from error
        if checkpoint is not None:
            try:
                _discard_checkpoint(*_checkpoint_paths(output))
            except OSError:
                pass

        return SyncResult(
            status="partial" if errors else "ok",
            output_path=output,
            updated=sum(outcome.status == "updated" for outcome in outcomes),
            unchanged=sum(outcome.status == "unchanged" for outcome in outcomes),
            moved=sum(outcome.status == "moved" for outcome in outcomes),
            deleted=deleted,
            pages=tuple(outcomes),
            errors=tuple(errors),
        )

    def _sync_selection(
        self,
        *,
        staging: Path,
        previous: dict[str, Any],
        next_manifest: dict[str, Any],
        selection: Selection,
        checkpoint: _Checkpoint | None,
        checkpoint_resumed: bool,
    ) -> tuple[list[PageOutcome], int, list[str]]:
        space = self.client.get_space(selection.space_key)
        missing: set[int] = set()
        if not selection.complete_space and not selection.descendants:
            direct_candidates: list[PageCandidate] = []
            for page_id in selection.page_ids:
                try:
                    direct_candidates.append(self.client.get_page(space.id, page_id))
                except GiteeWikiError:
                    missing.add(page_id)
            candidates = tuple(direct_candidates)
        else:
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
        next_space = (
            {}
            if checkpoint_resumed
            else copy.deepcopy(previous_space)
            if isinstance(previous_space, dict)
            else {}
        )
        next_space.update({"id": space.id, "key": space.key, "name": space.name})
        next_pages = next_space.setdefault("pages", {})
        if not isinstance(next_pages, dict):
            next_pages = {}
            next_space["pages"] = next_pages
        next_spaces[space.key] = next_space
        if checkpoint is not None:
            checkpoint.persist_space(space.key, next_space)

        outcomes: list[PageOutcome] = []
        errors: list[str] = []
        selected_ids: set[str] = set()
        if len(candidates) >= 20:
            with ThreadPoolExecutor(max_workers=12, thread_name_prefix="gwme-page-state") as pool:
                remote_states = tuple(
                    pool.map(
                        lambda candidate: self._read_page_state(space.id, candidate.page_id),
                        candidates,
                    )
                )
        else:
            remote_states = tuple(
                self._read_page_state(space.id, candidate.page_id) for candidate in candidates
            )
        for candidate, remote_state in zip(candidates, remote_states, strict=True):
            page_key = str(candidate.page_id)
            selected_ids.add(page_key)
            old_entry = previous_pages.get(page_key, {})
            if not isinstance(old_entry, dict):
                old_entry = {}
            revision = remote_state.revision
            desired_path = render_page_path(
                self.settings.page_path,
                space_name=space.name,
                ancestors=candidate.ancestors,
                page_title=candidate.title,
                page_id=candidate.page_id,
            )
            status, entry, page_errors = self._sync_page(
                staging=staging,
                space=space,
                candidate=candidate,
                revision=revision,
                attachments=remote_state.attachments,
                desired_path=desired_path,
                old_entry=old_entry,
                checkpoint_resumed=checkpoint_resumed,
                save_progress=(
                    (
                        lambda progress_entry, page_key=page_key: _save_checkpoint_page(
                            checkpoint=checkpoint,
                            space_key=space.key,
                            space=next_space,
                            pages=next_pages,
                            page_key=page_key,
                            entry=progress_entry,
                        )
                    )
                    if checkpoint is not None
                    else None
                ),
            )
            next_pages[page_key] = entry
            if checkpoint is not None:
                checkpoint.persist_page(space.key, next_space, page_key, entry)
            errors.extend(page_errors)
            outcomes.append(
                PageOutcome(
                    page_id=candidate.page_id,
                    status=status,
                    path=desired_path,
                    revision=str(revision),
                )
            )

        deleted = 0
        if selection.complete_space and (selection.cleanup_stale or checkpoint_resumed):
            for page_key in set(previous_pages) - selected_ids:
                old_entry = previous_pages.get(page_key)
                if isinstance(old_entry, dict):
                    _remove_managed_entry(staging, old_entry)
                next_pages.pop(page_key, None)
                if checkpoint is not None:
                    checkpoint.delete_page(space.key, page_key)
                if selection.cleanup_stale:
                    deleted += 1
        return outcomes, deleted, errors

    def _read_page_state(self, space_id: int, page_id: int) -> _PageRemoteState:
        return _PageRemoteState(
            revision=self.client.latest_revision(space_id, page_id),
            attachments=self.client.list_attachments(page_id),
        )

    def _sync_page(
        self,
        *,
        staging: Path,
        space: Space,
        candidate: PageCandidate,
        revision: int,
        attachments: tuple[Attachment, ...],
        desired_path: Path,
        old_entry: dict[str, Any],
        checkpoint_resumed: bool,
        save_progress: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[str, dict[str, Any], list[str]]:
        old_path = _manifest_path(old_entry.get("path"))
        old_attachments = old_entry.get("attachments", [])
        old_diagrams = old_entry.get("diagrams", [])
        old_revision = str(old_entry.get("revision", ""))
        attachments_unchanged = _attachments_match(
            staging=staging,
            old_attachments=old_attachments,
            current_attachments=attachments,
        )
        base_unchanged = (
            (self.settings.skip_unchanged or checkpoint_resumed)
            and old_entry.get(_CHECKPOINT_PARTIAL) is not True
            and old_revision == str(revision)
            and old_entry.get("rendererVersion") == _MARKDOWN_RENDERER_VERSION
            and old_entry.get("title") == candidate.title
            and old_path is not None
            and (staging / old_path).is_file()
            and attachments_unchanged
            and old_entry.get("diagramsComplete", True) is True
            and _diagrams_exist(staging, old_diagrams)
        )
        old_diagrams_by_id = _diagram_entries_by_id(old_diagrams)
        diagram_components: dict[int, DiagramComponent] = {}
        diagram_probe_errors: dict[int, GiteeWikiError] = {}
        diagrams_unchanged = True
        if base_unchanged:
            for component_id, old_diagram in old_diagrams_by_id.items():
                try:
                    component = self.client.get_diagram_component(space.key, component_id)
                except GiteeWikiError as error:
                    diagram_probe_errors[component_id] = error
                    diagrams_unchanged = False
                    continue
                diagram_components[component_id] = component
                digest = hashlib.sha256(component.content.encode("utf-8")).hexdigest()
                if old_diagram.get("sha256") != digest:
                    diagrams_unchanged = False
        unchanged = base_unchanged and diagrams_unchanged
        if unchanged and old_path == desired_path:
            entry = copy.deepcopy(old_entry)
            entry.update(_page_metadata(candidate, revision, desired_path))
            return "unchanged", entry, []
        if unchanged and old_path is not None:
            entry = self._move_unchanged_page(
                staging=staging,
                candidate=candidate,
                revision=revision,
                old_path=old_path,
                desired_path=desired_path,
                old_attachments=old_attachments,
                old_diagrams=old_diagrams,
            )
            return "moved", entry, []

        page = self.client.get_revision(space.id, candidate.page_id, revision)
        attachment_entries: list[dict[str, object]] = []
        diagram_entries: list[dict[str, object]] = []
        errors: list[str] = []
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
            sources = _attachment_source_variants(self.client.base_url, attachment.url)
            if old_attachment is not None and isinstance(old_attachment.get("urlPath"), str):
                sources.update(
                    _attachment_source_variants(
                        self.client.base_url, str(old_attachment["urlPath"])
                    )
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
                try:
                    content, content_type = self.client.download_attachment(
                        attachment.url, max_bytes=self.settings.max_attachment_bytes
                    )
                except GiteeWikiError as error:
                    remote_link = self.client.base_url.rstrip("/") + _attachment_url_path(
                        attachment.url
                    )
                    for source in sources:
                        replacements[source] = remote_link
                    errors.append(
                        f"page {candidate.page_id} attachment {attachment.id} skipped: {error}"
                    )
                    continue
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
            for source in sources:
                replacements[source] = relative_link
            attachment_entries.append(attachment_entry)
            if save_progress is not None:
                save_progress(
                    _checkpoint_page_entry(
                        candidate=candidate,
                        revision=revision,
                        desired_path=desired_path,
                        attachments=attachment_entries,
                        diagrams=diagram_entries,
                    )
                )

        diagram_links: dict[int, tuple[str, ...]] = {}
        diagrams_complete = True
        for component_id, updated_at in find_diagram_references(page.content):
            try:
                if component_id in diagram_probe_errors:
                    raise diagram_probe_errors[component_id]
                component = diagram_components.get(component_id)
                if component is None:
                    component = self.client.get_diagram_component(space.key, component_id)
                digest = hashlib.sha256(component.content.encode("utf-8")).hexdigest()
                old_diagram = old_diagrams_by_id.get(component_id)
                old_paths = _diagram_paths(old_diagram)
                if (
                    old_diagram is not None
                    and old_diagram.get("sha256") == digest
                    and old_paths
                    and all((staging / path).is_file() for path in old_paths)
                ):
                    svgs: tuple[str, ...] | None = None
                    page_count = len(old_paths)
                else:
                    svgs = self.diagram_renderer.render(component.content)
                    if not svgs:
                        raise DiagramRenderError("local browser returned no diagram pages")
                    page_count = len(svgs)

                paths = tuple(
                    render_diagram_path(
                        self.settings.diagram_path,
                        page_path=desired_path,
                        page_title=candidate.title,
                        diagram_id=component_id,
                        diagram_page=index,
                    )
                    for index in range(1, page_count + 1)
                )
                if svgs is None:
                    for old_diagram_path, diagram_path in zip(old_paths, paths, strict=True):
                        _copy_managed_file(staging, old_diagram_path, diagram_path)
                else:
                    for diagram_path, svg in zip(paths, svgs, strict=True):
                        _atomic_write_text(staging / diagram_path, svg)
                diagram_links[component_id] = tuple(
                    _relative_link(desired_path.parent, path) for path in paths
                )
                diagram_entries.append(
                    {
                        "id": component_id,
                        "updatedAt": updated_at,
                        "sha256": digest,
                        "paths": [path.as_posix() for path in paths],
                    }
                )
                if save_progress is not None:
                    save_progress(
                        _checkpoint_page_entry(
                            candidate=candidate,
                            revision=revision,
                            desired_path=desired_path,
                            attachments=attachment_entries,
                            diagrams=diagram_entries,
                        )
                    )
            except (GiteeWikiError, DiagramRenderError) as error:
                diagrams_complete = False
                errors.append(f"page {candidate.page_id} diagram {component_id} skipped: {error}")
                old_diagram = old_diagrams_by_id.get(component_id)
                old_paths = _diagram_paths(old_diagram)
                if (
                    old_diagram is not None
                    and old_paths
                    and all((staging / path).is_file() for path in old_paths)
                ):
                    paths = tuple(
                        render_diagram_path(
                            self.settings.diagram_path,
                            page_path=desired_path,
                            page_title=candidate.title,
                            diagram_id=component_id,
                            diagram_page=index,
                        )
                        for index in range(1, len(old_paths) + 1)
                    )
                    for old_diagram_path, diagram_path in zip(old_paths, paths, strict=True):
                        _copy_managed_file(staging, old_diagram_path, diagram_path)
                    diagram_links[component_id] = tuple(
                        _relative_link(desired_path.parent, path) for path in paths
                    )
                    preserved = copy.deepcopy(old_diagram)
                    preserved["paths"] = [path.as_posix() for path in paths]
                    diagram_entries.append(preserved)
                    if save_progress is not None:
                        save_progress(
                            _checkpoint_page_entry(
                                candidate=candidate,
                                revision=revision,
                                desired_path=desired_path,
                                attachments=attachment_entries,
                                diagrams=diagram_entries,
                            )
                        )

        body = _rewrite_links(
            render_wiki_content(page.content, diagram_links=diagram_links), replacements
        )
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
        current_diagram_paths = {
            path for entry in diagram_entries for path in _diagram_paths(entry)
        }
        for old_diagram in old_diagrams if isinstance(old_diagrams, list) else []:
            if isinstance(old_diagram, dict):
                for old_diagram_path in _diagram_paths(old_diagram):
                    if old_diagram_path not in current_diagram_paths:
                        _remove_path(staging, old_diagram_path)
        _atomic_write_text(staging / desired_path, document)
        entry = _page_metadata(candidate, revision, desired_path)
        entry["attachments"] = attachment_entries
        entry["diagrams"] = diagram_entries
        entry["diagramsComplete"] = diagrams_complete
        moved = (
            old_revision == str(revision)
            and old_path is not None
            and old_path != desired_path
            and attachments_unchanged
        )
        return ("moved" if moved else "updated"), entry, errors

    def _move_unchanged_page(
        self,
        *,
        staging: Path,
        candidate: PageCandidate,
        revision: int,
        old_path: Path,
        desired_path: Path,
        old_attachments: object,
        old_diagrams: object,
    ) -> dict[str, Any]:
        old_document = (staging / old_path).read_text(encoding="utf-8")
        attachment_entries: list[dict[str, object]] = []
        diagram_entries: list[dict[str, object]] = []
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
        if isinstance(old_diagrams, list):
            for item in old_diagrams:
                if not isinstance(item, dict):
                    continue
                component_id = int(item["id"])
                old_paths = _diagram_paths(item)
                new_paths = tuple(
                    render_diagram_path(
                        self.settings.diagram_path,
                        page_path=desired_path,
                        page_title=candidate.title,
                        diagram_id=component_id,
                        diagram_page=index,
                    )
                    for index in range(1, len(old_paths) + 1)
                )
                for old_diagram_path, new_diagram_path in zip(old_paths, new_paths, strict=True):
                    replacements[_relative_link(old_path.parent, old_diagram_path)] = (
                        _relative_link(desired_path.parent, new_diagram_path)
                    )
                    _move_file(staging, old_diagram_path, new_diagram_path)
                copied = copy.deepcopy(item)
                copied["paths"] = [path.as_posix() for path in new_paths]
                diagram_entries.append(copied)
        _move_file(
            staging,
            old_path,
            desired_path,
            content=_rewrite_links(old_document, replacements),
        )
        entry = _page_metadata(candidate, revision, desired_path)
        entry["attachments"] = attachment_entries
        entry["diagrams"] = diagram_entries
        entry["diagramsComplete"] = True
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
    parsed = urlparse(url)
    relative = _attachment_url_path(url)
    suffix = (f"?{parsed.query}" if parsed.query else "") + (
        f"#{parsed.fragment}" if parsed.fragment else ""
    )
    source_path = parsed.path
    absolute = base_url.rstrip("/") + relative
    return {
        url,
        source_path,
        source_path + suffix,
        relative,
        relative + suffix,
        absolute,
        absolute + suffix,
    } - {""}


def _attachment_url_path(url: str) -> str:
    relative = urlparse(url).path
    if not relative.startswith("/wiki-static/"):
        relative = "/wiki-static/" + relative.lstrip("/")
    return relative


def _rewrite_links(content: str, replacements: dict[str, str]) -> str:
    placeholders: list[tuple[str, str]] = []
    for index, source in enumerate(sorted(replacements, key=len, reverse=True)):
        placeholder = f"\x00gwme-attachment-link-{index}\x00"
        replacement = replacements[source]
        if "?" in source or "#" in source:
            updated = content.replace(source, placeholder)
            if updated != content:
                placeholders.append((placeholder, replacement))
            content = updated
            continue
        suffix = r"(?:\?[^)\s<>\"']*)?(?:#[^)\s<>\"']*)?"
        content, count = re.subn(
            re.escape(source) + suffix,
            placeholder,
            content,
        )
        if count:
            placeholders.append((placeholder, replacement))
    for placeholder, replacement in placeholders:
        content = content.replace(placeholder, replacement)
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


def _diagram_entries_by_id(value: object) -> dict[int, dict[str, Any]]:
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


def _diagram_paths(value: object) -> tuple[Path, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("paths"), list):
        return ()
    result: list[Path] = []
    for raw_path in value["paths"]:
        path = _manifest_path(raw_path)
        if path is None:
            return ()
        result.append(path)
    return tuple(result)


def _diagrams_exist(staging: Path, value: object) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(item, dict)
        and bool(paths := _diagram_paths(item))
        and all((staging / path).is_file() for path in paths)
        for item in value
    )


def _attachment_metadata(attachment: Attachment, attachment_path: Path) -> dict[str, object]:
    return {
        "id": attachment.id,
        "name": attachment.name,
        "path": attachment_path.as_posix(),
        "urlPath": _attachment_url_path(attachment.url),
        "remoteSize": attachment.size,
        "remoteContentType": attachment.content_type,
        "remoteUpdatedAt": attachment.updated_at,
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
        and entry.get("remoteUpdatedAt") == attachment.updated_at
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
    diagrams = entry.get("diagrams", [])
    if isinstance(diagrams, list):
        for diagram in diagrams:
            for diagram_path in _diagram_paths(diagram):
                _remove_path(staging, diagram_path)


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
