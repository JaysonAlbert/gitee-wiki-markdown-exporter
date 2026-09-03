"""Provider-neutral data models used by the exporter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Space:
    """A resolved Gitee Wiki space."""

    id: int
    key: str
    name: str


@dataclass(frozen=True)
class TreeNode:
    """A page and its children in a Wiki tree."""

    page_id: int
    title: str
    parent_id: int | str | None = None
    children: tuple[TreeNode, ...] = ()


@dataclass(frozen=True)
class PageRevision:
    """A concrete immutable page revision."""

    id: int
    title: str
    content_type: str
    content: str


@dataclass(frozen=True)
class Attachment:
    """Attachment metadata returned by Gitee."""

    id: int
    name: str
    url: str
    size: int | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class PageCandidate:
    """A selected tree page with path context."""

    page_id: int
    title: str
    parent_id: int | str | None
    ancestors: tuple[str, ...]
    ancestor_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PageOutcome:
    """Result of synchronizing one page."""

    page_id: int
    status: str
    path: Path
    revision: str


@dataclass(frozen=True)
class SyncResult:
    """Stable summary returned by a synchronization run."""

    status: str
    output_path: Path
    updated: int = 0
    unchanged: int = 0
    moved: int = 0
    deleted: int = 0
    pages: tuple[PageOutcome, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """Render a JSON-safe summary."""
        return {
            "schema": "gitee_wiki_markdown_export_v1",
            "status": self.status,
            "outputPath": str(self.output_path),
            "updated": self.updated,
            "unchanged": self.unchanged,
            "moved": self.moved,
            "deleted": self.deleted,
            "pages": [
                {
                    "pageId": page.page_id,
                    "status": page.status,
                    "path": page.path.as_posix(),
                    "revision": page.revision,
                }
                for page in self.pages
            ],
            "errors": list(self.errors),
        }
