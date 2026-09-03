"""Versioned local synchronization manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class ManifestError(ValueError):
    """Raised when an existing lockfile cannot be trusted."""


def empty_manifest() -> dict[str, Any]:
    """Return an empty current-schema manifest."""
    return {"schemaVersion": SCHEMA_VERSION, "provider": "gitee-project-wiki", "spaces": {}}


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a manifest or return an empty one when it does not exist."""
    if not path.exists():
        return empty_manifest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError(f"unsupported manifest schema in {path}")
    if payload.get("provider") != "gitee-project-wiki" or not isinstance(
        payload.get("spaces"), dict
    ):
        raise ManifestError(f"invalid Gitee Wiki manifest in {path}")
    return payload


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Write a manifest atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
