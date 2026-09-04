import json
from pathlib import Path

from typer.testing import CliRunner

import gitee_wiki_markdown_exporter.cli as cli_module
from gitee_wiki_markdown_exporter.cli import app
from gitee_wiki_markdown_exporter.models import SyncResult

runner = CliRunner()


def test_version_exposes_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "gitee-wiki-markdown-exporter 0.3.0\n"


def test_config_show_redacts_inline_token(tmp_path: Path) -> None:
    config_path = tmp_path / "app_data.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {
                    "gitee": {
                        "url": "https://gitee.example.com",
                        "tenant_id": "demo",
                        "api_token": "inline-secret",
                    }
                },
                "export": {"output_path": "mirror"},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "--config-path", str(config_path)])

    assert result.exit_code == 0
    assert "inline-secret" not in result.stdout
    assert '"api_token": "***"' in result.stdout


def test_partial_export_reports_skipped_attachment_warning(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "app_data.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {
                    "gitee": {
                        "url": "https://gitee.example.com",
                        "tenant_id": "demo",
                        "api_token": "test-token",
                    }
                },
                "export": {"output_path": "mirror"},
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

    class PartialExporter:
        def __init__(self, **_kwargs) -> None:
            pass

        def sync_pages(self, *_args, **_kwargs) -> SyncResult:
            return SyncResult(
                status="partial",
                output_path=tmp_path / "mirror",
                updated=1,
                errors=("page 2 attachment 99 skipped: HTTP 500",),
            )

    monkeypatch.setattr(cli_module, "GiteeWikiClient", FakeClient)
    monkeypatch.setattr(cli_module, "WikiExporter", PartialExporter)

    result = runner.invoke(
        app,
        ["pages", "2", "--space", "ENG", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0
    assert "1 resources skipped" in result.stdout
    assert "warning: page 2 attachment 99 skipped: HTTP 500" in result.stderr
