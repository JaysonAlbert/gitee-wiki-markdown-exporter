import json
from pathlib import Path

from typer.testing import CliRunner

from gitee_wiki_markdown_exporter.cli import app

runner = CliRunner()


def test_version_exposes_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "gitee-wiki-markdown-exporter 0.1.1\n"


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
