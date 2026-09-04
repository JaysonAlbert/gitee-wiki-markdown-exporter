"""Configuration loading with safe credential handling."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

CONFIG_ENV = "GWME_CONFIG_PATH"
DEFAULT_TOKEN_ENV = "GITEE_PROJECT_WIKI_ACCESS_TOKEN"


class ConfigError(ValueError):
    """Raised when exporter configuration is missing or invalid."""


@dataclass(frozen=True)
class AuthSettings:
    """Gitee Project Wiki connection identity."""

    url: str
    tenant_id: str
    api_token: str | None = None
    api_token_env: str = DEFAULT_TOKEN_ENV

    def resolve_token(self) -> str:
        """Resolve the bearer token without placing it in rendered settings."""
        token = os.environ.get(self.api_token_env) or self.api_token
        if not token:
            raise ConfigError(
                f"Gitee Wiki token is missing; set environment variable {self.api_token_env}"
            )
        return token


@dataclass(frozen=True)
class ConnectionSettings:
    """HTTP behavior."""

    timeout: float = 30.0
    verify_ssl: bool = True


@dataclass(frozen=True)
class ExportSettings:
    """Local mirror behavior and layout."""

    output_path: Path
    page_path: str = "{space_name}/{ancestor_titles}/{page_title}-{page_id}.md"
    attachment_path: str = (
        "{page_parent_path}/{page_title}/{attachment_file_id}{attachment_extension}"
    )
    diagram_path: str = (
        "{page_parent_path}/{page_title}/diagram-{diagram_id}-{diagram_page}.svg"
    )
    include_document_title: bool = True
    include_yaml_frontmatter: bool = False
    skip_unchanged: bool = True
    cleanup_stale: bool = True
    lockfile_name: str = "gitee-wiki-lock.json"
    max_attachment_bytes: int = 50 * 1024 * 1024

    def with_output_path(self, output_path: Path | None) -> ExportSettings:
        """Return settings with an optional CLI output override."""
        return replace(self, output_path=output_path.resolve()) if output_path else self


@dataclass(frozen=True)
class SyncSettings:
    """Configured targets for the schedulable sync command."""

    spaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    """Complete application settings."""

    auth: AuthSettings
    connection: ConnectionSettings
    export: ExportSettings
    sync: SyncSettings
    config_path: Path


def default_config_path() -> Path:
    """Return the platform-native default configuration path."""
    return user_config_path("gitee-wiki-markdown-exporter") / "app_data.json"


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate JSON configuration."""
    actual_path = (path or Path(os.environ.get(CONFIG_ENV, default_config_path()))).expanduser()
    if not actual_path.exists():
        raise ConfigError(
            f"configuration file not found: {actual_path}; set {CONFIG_ENV} or create the file"
        )
    try:
        payload = json.loads(actual_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration {actual_path}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigError("configuration root must be a JSON object")

    auth_data = _mapping(_mapping(payload.get("auth"), "auth").get("gitee"), "auth.gitee")
    url = _required_text(auth_data.get("url"), "auth.gitee.url").rstrip("/")
    tenant_id = _required_text(auth_data.get("tenant_id"), "auth.gitee.tenant_id")
    connection_data = _optional_mapping(payload.get("connection_config"))
    export_data = _mapping(payload.get("export"), "export")
    sync_data = _optional_mapping(payload.get("sync"))

    raw_output = _required_text(export_data.get("output_path"), "export.output_path")
    output_path = Path(raw_output).expanduser()
    if not output_path.is_absolute():
        output_path = actual_path.parent / output_path

    settings = Settings(
        auth=AuthSettings(
            url=url,
            tenant_id=tenant_id,
            api_token=_optional_text(auth_data.get("api_token")),
            api_token_env=_optional_text(auth_data.get("api_token_env")) or DEFAULT_TOKEN_ENV,
        ),
        connection=ConnectionSettings(
            timeout=_positive_float(
                connection_data.get("timeout", 30), "connection_config.timeout"
            ),
            verify_ssl=_boolean(
                connection_data.get("verify_ssl", True), "connection_config.verify_ssl"
            ),
        ),
        export=ExportSettings(
            output_path=output_path.resolve(),
            page_path=str(
                export_data.get("page_path")
                or "{space_name}/{ancestor_titles}/{page_title}-{page_id}.md"
            ),
            attachment_path=str(
                export_data.get("attachment_path")
                or "{page_parent_path}/{page_title}/{attachment_file_id}{attachment_extension}"
            ),
            diagram_path=str(
                export_data.get("diagram_path")
                or "{page_parent_path}/{page_title}/diagram-{diagram_id}-{diagram_page}.svg"
            ),
            include_document_title=_boolean(
                export_data.get("include_document_title", True),
                "export.include_document_title",
            ),
            include_yaml_frontmatter=_boolean(
                export_data.get("include_yaml_frontmatter", False),
                "export.include_yaml_frontmatter",
            ),
            skip_unchanged=_boolean(
                export_data.get("skip_unchanged", True), "export.skip_unchanged"
            ),
            cleanup_stale=_boolean(export_data.get("cleanup_stale", True), "export.cleanup_stale"),
            lockfile_name=_required_text(
                export_data.get("lockfile_name", "gitee-wiki-lock.json"),
                "export.lockfile_name",
            ),
            max_attachment_bytes=_positive_int(
                export_data.get("max_attachment_bytes", 50 * 1024 * 1024),
                "export.max_attachment_bytes",
            ),
        ),
        sync=SyncSettings(spaces=_string_tuple(sync_data.get("spaces", []), "sync.spaces")),
        config_path=actual_path.resolve(),
    )
    if Path(settings.export.lockfile_name).name != settings.export.lockfile_name:
        raise ConfigError("export.lockfile_name must be a filename, not a path")
    return settings


def safe_settings_dict(settings: Settings) -> dict[str, object]:
    """Render settings without exposing a configured or environment token."""
    return {
        "configPath": str(settings.config_path),
        "auth": {
            "gitee": {
                "url": settings.auth.url,
                "tenant_id": settings.auth.tenant_id,
                "api_token": "***" if settings.auth.api_token else None,
                "api_token_env": settings.auth.api_token_env,
                "token_available": bool(
                    os.environ.get(settings.auth.api_token_env) or settings.auth.api_token
                ),
            }
        },
        "connection_config": {
            "timeout": settings.connection.timeout,
            "verify_ssl": settings.connection.verify_ssl,
        },
        "export": {
            "output_path": str(settings.export.output_path),
            "page_path": settings.export.page_path,
            "attachment_path": settings.export.attachment_path,
            "diagram_path": settings.export.diagram_path,
            "include_document_title": settings.export.include_document_title,
            "include_yaml_frontmatter": settings.export.include_yaml_frontmatter,
            "skip_unchanged": settings.export.skip_unchanged,
            "cleanup_stale": settings.export.cleanup_stale,
            "lockfile_name": settings.export.lockfile_name,
            "max_attachment_bytes": settings.export.max_attachment_bytes,
        },
        "sync": {"spaces": list(settings.sync.spaces)},
    }


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a JSON object")
    return value


def _optional_mapping(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    return _mapping(value, "configuration section")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be true or false")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{label} must be a positive integer") from error
    if parsed <= 0:
        raise ConfigError(f"{label} must be a positive integer")
    return parsed


def _positive_float(value: object, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{label} must be positive") from error
    if parsed <= 0:
        raise ConfigError(f"{label} must be positive")
    return parsed


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigError(f"{label} must be an array of non-empty strings")
    return tuple(dict.fromkeys(item.strip() for item in value))
