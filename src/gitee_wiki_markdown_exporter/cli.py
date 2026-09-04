"""Command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from gitee_wiki_markdown_exporter import __version__
from gitee_wiki_markdown_exporter.client import GiteeWikiClient, GiteeWikiError
from gitee_wiki_markdown_exporter.config import (
    ConfigError,
    Settings,
    load_settings,
    safe_settings_dict,
)
from gitee_wiki_markdown_exporter.exporter import ExportError, WikiExporter
from gitee_wiki_markdown_exporter.models import SyncResult

app = typer.Typer(
    no_args_is_help=True,
    help="Export Gitee Project Wiki pages to an incremental local Markdown mirror.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"gitee-wiki-markdown-exporter {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Export Gitee Project Wiki content."""


@app.command(help="Export one or more pages by ID.")
def pages(
    page_ids: Annotated[list[int], typer.Argument(help="Page ID(s)")],
    space: Annotated[str, typer.Option("--space", help="Gitee Wiki space key")],
    output_path: Annotated[Path | None, typer.Option("--output-path")] = None,
    config_path: Annotated[Path | None, typer.Option("--config-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export selected pages."""
    _execute(
        config_path=config_path,
        output_path=output_path,
        json_output=json_output,
        operation=lambda exporter, _settings: exporter.sync_pages(space, tuple(page_ids)),
    )


@app.command(name="pages-with-descendants", help="Export pages and all their descendants.")
def pages_with_descendants(
    page_ids: Annotated[list[int], typer.Argument(help="Page ID(s)")],
    space: Annotated[str, typer.Option("--space", help="Gitee Wiki space key")],
    output_path: Annotated[Path | None, typer.Option("--output-path")] = None,
    config_path: Annotated[Path | None, typer.Option("--config-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export selected page subtrees."""
    _execute(
        config_path=config_path,
        output_path=output_path,
        json_output=json_output,
        operation=lambda exporter, _settings: exporter.sync_pages(
            space, tuple(page_ids), descendants=True
        ),
    )


@app.command(help="Export all pages from one or more spaces.")
def spaces(
    space_keys: Annotated[list[str], typer.Argument(help="Space key(s)")],
    output_path: Annotated[Path | None, typer.Option("--output-path")] = None,
    config_path: Annotated[Path | None, typer.Option("--config-path")] = None,
    cleanup_stale: Annotated[bool, typer.Option("--cleanup-stale/--no-cleanup-stale")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export complete spaces."""
    _execute(
        config_path=config_path,
        output_path=output_path,
        json_output=json_output,
        operation=lambda exporter, _settings: exporter.sync_spaces(
            tuple(space_keys), cleanup_stale=cleanup_stale
        ),
    )


@app.command(help="Synchronize the spaces configured in sync.spaces.")
def sync(
    output_path: Annotated[Path | None, typer.Option("--output-path")] = None,
    config_path: Annotated[Path | None, typer.Option("--config-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the configured schedulable synchronization."""

    def operation(exporter: WikiExporter, settings: Settings) -> SyncResult:
        if not settings.sync.spaces:
            raise ConfigError("sync.spaces is empty; configure at least one space")
        return exporter.sync_spaces(settings.sync.spaces)

    _execute(
        config_path=config_path,
        output_path=output_path,
        json_output=json_output,
        operation=operation,
    )


@app.command(help="Display the resolved configuration with credentials redacted.")
def config(
    config_path: Annotated[Path | None, typer.Option("--config-path")] = None,
    show: Annotated[bool, typer.Option("--show")] = True,
) -> None:
    """Show safe configuration."""
    del show
    try:
        settings = load_settings(config_path)
    except ConfigError as error:
        _fail(str(error), code=2)
    typer.echo(json.dumps(safe_settings_dict(settings), ensure_ascii=False, indent=2))


def _execute(
    *,
    config_path: Path | None,
    output_path: Path | None,
    json_output: bool,
    operation: object,
) -> None:
    try:
        settings = load_settings(config_path)
        export_settings = settings.export.with_output_path(output_path)
        with GiteeWikiClient(
            base_url=settings.auth.url,
            tenant_id=settings.auth.tenant_id,
            token=settings.auth.resolve_token(),
            timeout=settings.connection.timeout,
            verify_ssl=settings.connection.verify_ssl,
        ) as client:
            exporter = WikiExporter(client=client, settings=export_settings)
            result = operation(exporter, settings)  # type: ignore[operator]
    except ConfigError as error:
        _fail(str(error), code=2)
    except (GiteeWikiError, ExportError, OSError, ValueError) as error:
        _fail(str(error), code=1)
    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(
            "Gitee Wiki export complete: "
            f"{result.updated} updated, {result.unchanged} unchanged, "
            f"{result.moved} moved, {result.deleted} deleted; "
            f"{len(result.errors)} attachments skipped; "
            f"output={result.output_path}"
        )
        for error in result.errors:
            typer.echo(f"warning: {error}", err=True)


def _fail(message: str, *, code: int) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)
