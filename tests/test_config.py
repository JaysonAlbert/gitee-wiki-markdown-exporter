import json
from pathlib import Path

from gitee_wiki_markdown_exporter.config import load_settings, safe_settings_dict


def test_load_settings_resolves_relative_output_and_environment_token(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "app_data.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {
                    "gitee": {
                        "url": "https://gitee.example.com/",
                        "tenant_id": "demo",
                        "api_token_env": "TEST_WIKI_TOKEN",
                    }
                },
                "export": {"output_path": "mirror"},
                "sync": {"spaces": ["ENG", "ENG"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_WIKI_TOKEN", "secret-value")

    settings = load_settings(config_path)

    assert settings.auth.url == "https://gitee.example.com"
    assert settings.auth.resolve_token() == "secret-value"
    assert settings.export.output_path == (tmp_path / "mirror").resolve()
    assert settings.sync.spaces == ("ENG",)
    rendered = safe_settings_dict(settings)
    assert "secret-value" not in json.dumps(rendered)
    assert rendered["auth"]["gitee"]["token_available"] is True
