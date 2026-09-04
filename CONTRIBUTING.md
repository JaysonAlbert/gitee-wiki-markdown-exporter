# Contributing

Issues and pull requests are welcome, especially for sanitized Gitee Project Wiki contract
fixtures from versions not covered yet.

## Development

```bash
git clone https://github.com/JaysonAlbert/gitee-wiki-markdown-exporter.git
cd gitee-wiki-markdown-exporter
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=gitee_wiki_markdown_exporter --cov-report=term-missing
```

Tests must not call a production Wiki. Use `httpx.MockTransport` for HTTP contracts and keep all
fixtures free of tokens, tenant identifiers, usernames, private page titles, page bodies, and
attachment URLs.

Behavior changes should include a test through the public client, exporter, or CLI boundary.
Internal refactors should rely on existing behavior tests rather than asserting implementation
details.
