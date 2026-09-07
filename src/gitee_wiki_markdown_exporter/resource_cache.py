"""Private downloaded-resource recovery cache; never a snapshot of the live mirror."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


class ResourceCache:
    """Use only while holding the owning output lock. Records contain no resource URLs."""

    def __init__(self, output: Path, scope: str, max_bytes: int) -> None:
        self.root = output.parent / f".{output.name}.resources"
        self.directory = self.root / scope
        self.max_bytes = max_bytes
        if self.root.is_symlink() or self.directory.is_symlink():
            raise OSError("resource cache must not be a symbolic link")
        if self.root.exists() and not self.directory.is_dir():
            self.clear()

    @staticmethod
    def key(identity: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def load(self, key: str) -> tuple[bytes, str | None] | None:
        payload = self.directory / f"{key}.bin"
        record = self.directory / f"{key}.json"
        try:
            if payload.is_symlink() or record.is_symlink() or record.stat().st_size > 4096:
                return None
            metadata = json.loads(record.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or payload.stat().st_size > self.max_bytes:
                return None
            content_type = metadata.get("contentType")
            if content_type is not None and not isinstance(content_type, str):
                return None
            with payload.open("rb") as stream:
                content = stream.read(self.max_bytes + 1)
            if (
                len(content) > self.max_bytes
                or len(content) != metadata.get("size")
                or hashlib.sha256(content).hexdigest() != metadata.get("sha256")
            ):
                return None
            return content, content_type
        except (OSError, ValueError):
            return None

    def save(self, key: str, content: bytes, content_type: str | None) -> None:
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        _atomic_write(self.directory / f"{key}.bin", content)
        record = {
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "contentType": content_type,
        }
        _atomic_write(self.directory / f"{key}.json", json.dumps(record).encode())

    def clear(self) -> None:
        if self.root.is_symlink():
            raise OSError("resource cache must not be a symbolic link")
        if self.root.exists():
            shutil.rmtree(self.root)


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
