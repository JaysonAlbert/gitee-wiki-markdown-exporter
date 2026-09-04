# AGENTS.md

This file is the operating guide for coding agents working in
`gitee-wiki-markdown-exporter`. Keep changes small, evidence-backed, and compatible with the
published CLI and PyPI package.

## Project scope

This repository provides a read-only Python CLI that mirrors Gitee **Project Wiki** content to
local Markdown. It targets the observed `/api/wiki/` gateway contract. It does not target
Git-backed repository wikis (`<repository>.wiki.git`).

The `sync` command is the schedulable primitive. Cron, systemd, GitHub Actions, or an external
orchestrator owns scheduling; do not add a resident daemon without an explicit design request.

## Read before changing code

Read only the documents relevant to the change:

| Change | Required context |
| --- | --- |
| User-facing behavior, setup, or commands | `README.md` |
| Synchronization or component boundaries | `docs/architecture.md` |
| Gitee endpoints, headers, or response parsing | `docs/compatibility.md` |
| Versioning, tags, GitHub Releases, or PyPI | `docs/releasing.md` |

Inspect `git status` before editing. Preserve unrelated user changes and do not rewrite published
history.

## Code ownership map

- `cli.py`: argument parsing, exit codes, and human/JSON rendering only.
- `config.py`: configuration loading, validation, defaults, and safe redaction.
- `client.py`: HTTP authentication, endpoint calls, pagination, response validation, download
  boundaries, and transport-error sanitization.
- `exporter.py`: page selection, incremental decisions, attachment localization, managed cleanup,
  staging, and output replacement.
- `diagram.py`: local browser discovery, draw.io rendering, network isolation, and SVG sanitizing.
- `rich_text.py`: observed Gitee rich-text JSON to Markdown rendering with plain-text passthrough.
- `paths.py`: filename normalization and cross-platform relative-path safety.
- `manifest.py`: versioned lockfile loading and atomic writing.
- `models.py`: typed values exchanged across boundaries.

Keep these responsibilities separate. Do not move synchronization rules into the CLI or HTTP
details into the exporter.

## Non-negotiable invariants

### Read-only remote behavior

- Gitee requests must remain read-only. The attachment-list endpoint is `POST` because that is the
  observed query contract; it must not mutate remote state.
- Do not add create, update, delete, or publish-to-Gitee behavior as an incidental extension.
- Never run tests against a production Wiki. New live-contract checks must use an explicitly
  supplied non-production tenant and must not be part of the default test suite.

### Credentials and network safety

- Prefer `GITEE_PROJECT_WIKI_ACCESS_TOKEN` or another configured environment variable. Never
  commit tokens or credential-bearing `app_data.json` files.
- Keep this public repository free of employer-private domains, tenant identifiers, space or page
  identifiers, system names, document titles, page bodies, and attachment metadata. Tests and
  examples must use neutral synthetic values such as `example.com`.
- Live validation may use an explicitly supplied private Wiki only through read-only calls. Keep
  its configuration and exported content outside the repository, and never promote captured
  payloads into fixtures, logs, documentation, or build artifacts.
- Never emit bearer tokens, authorization headers, tenant-sensitive payloads, page bodies, or real
  signed attachment URLs in errors, logs, JSON summaries, fixtures, or manifests. Sanitized fake
  credentials are allowed in regression tests.
- Error URLs must omit userinfo, query strings, and fragments. The manifest may store only the
  normalized attachment `urlPath`, never the raw URL.
- Attachment downloads must remain on the configured Gitee origin, must not follow arbitrary
  redirects, and must enforce `max_attachment_bytes` while streaming.

### Filesystem and transaction safety

- Treat configuration templates, remote titles, attachment names, and manifest paths as untrusted
  path input.
- Validate both POSIX and Windows path semantics. Reject absolute paths, drives, backslashes in
  templates, and `..` traversal before joining with the output root.
- Cleanup may remove only files recorded as managed by `gitee-wiki-lock.json`. Never recursively
  clean the whole output tree or remove untracked user files.
- Build a complete staging mirror before replacing the live output. A failure before the swap must
  leave the previous mirror and manifest intact.
- Staging can contain hard links to the previous mirror. Never modify a reused file in place;
  write to a temporary file and replace it, or unlink/copy safely first.
- Backup deletion after a successful swap is best-effort and must not turn a committed export into
  a reported failure.

### Incremental synchronization

- Page IDs and revision IDs are the stable remote identity. Tree titles and ancestors determine
  the desired local path.
- Poll attachment metadata on every selected-page sync, even when the page revision is unchanged.
  Reuse attachment bytes only when the recorded metadata and managed file still match.
- Poll recorded draw.io component content hashes even when the host-page revision is unchanged.
  Preserve the last successful SVG after a transient component fetch or render failure.
- A title or tree-path change must update generated Markdown metadata and local attachment links.
- A complete-space sync may reconcile deleted pages; a selected-page sync must not infer that
  unselected pages were deleted.
- Preserve the documented limitation: a byte-only attachment replacement is not detectable when
  Gitee changes none of ID, name, normalized URL path, advertised size, content type, or upload
  timestamp.

### Compatibility

- Gitee Project Wiki endpoints are observed, not part of the public Gitee v5 OpenAPI. Do not guess
  response variants or silently accept malformed envelopes.
- Endpoint or parser changes require a sanitized HTTP contract test and an update to
  `docs/compatibility.md`.
- Treat command names, options, exit codes, configuration keys/defaults, JSON result schema, and
  manifest semantics as public contracts. Preserve compatibility unless the requested version
  change explicitly permits a break.
- Keep `gitee-wiki-markdown-exporter` and `gw-export` as equivalent console entry points.
- Maintain Python 3.10 compatibility and avoid unnecessary dependencies or abstractions.

## Testing and verification

Tests should assert observable behavior through the client, exporter, configuration, or CLI
boundary. Avoid tests that only freeze private helper structure.

For a behavior change or bug fix, first add a focused regression test that fails for the intended
reason, then implement the smallest fix. Pure documentation or workflow metadata changes normally
do not need new test code.

Run the checks appropriate to the change:

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=gitee_wiki_markdown_exporter --cov-report=term-missing
```

For packaging, dependency, version, or release changes, also run:

```bash
uv lock --check
uv build
uvx --from twine twine check dist/*
```

For CLI-facing changes, smoke-test both invocation forms:

```bash
uv run gw-export --help
uv run python -m gitee_wiki_markdown_exporter --help
```

Report exactly what ran. Do not describe skipped, simulated, or inapplicable checks as passing.

## Documentation and release discipline

- Update `README.md` when installation, configuration, commands, outputs, or operational behavior
  changes. Update architecture or compatibility docs when their owned contracts change.
- The version in `pyproject.toml` is the package version source. Runtime version reporting reads
  installed package metadata; do not add a second version constant.
- Use semantic versions. Update `CHANGELOG.md` with the version before release.
- Merging to `main` runs CI but does not publish. Publishing occurs only from a non-draft GitHub
  Release whose `vX.Y.Z` tag exactly matches `project.version`.
- PyPI releases are immutable. Never reuse a published version or move a published release tag;
  fix forward with a new version.
- Keep PyPI publishing on GitHub OIDC Trusted Publishing. Do not add a long-lived PyPI token to the
  repository or GitHub secrets.
