# Export usability improvements

This L1 task record extends the 0.4.0 architecture and the recommendations in
[Confluence feature comparison](confluence-feature-comparison.md). The user requested the high
and medium priority improvements. Implementation stays in the read-only client, renderer and
exporter boundaries. Low-priority attachment modes, comments and reader-specific plugins are
outside this change. This is an incremental design; `architecture.md` remains the baseline.

## Transport retries

API queries and same-origin resource downloads make at most four attempts. Retry connection/read
timeouts, network failures, remote protocol failures and HTTP 408/429/500/502/503/504. The default
backoff is 0.5, 1 and 2 seconds. A valid `Retry-After` (seconds or HTTP date) takes precedence,
bounded to 30 seconds. Authentication failures, redirects, invalid JSON/envelopes, invalid images
and oversized bodies do not retry. Every retry starts an attachment from byte zero and applies
the same origin and streaming size bounds. Close responses before waiting. Retried attachment-list
POST requests are still read-only queries. Errors retain the existing sanitized format.

Acceptance: transient GET and attachment POST recovery, eventual exhaustion, retry delay parsing,
permanent errors without retries, and interrupted streaming without concatenating partial bytes.
Tests use synthetic HTTP responses and a patched sleeper; no live tenant is used.

## Navigation and metadata

Use exported page identities and paths to provide offline navigation, with unchanged source text
and anchor fragments. A target move must update its incoming links even if the referring page
revision does not change. A missing/unselected target must never become a broken local link.
Cross-space links must not depend on selection order. Keep generated navigation in the staging
transaction and preserve untracked user files.

Page breadcrumbs default on (`export.include_page_breadcrumbs`) and use parsed ancestor IDs/titles;
link to managed ancestors available in
the mirror and retain plain labels for unavailable ancestors. Extend optional YAML with existing
space/parent/path context, safely quote strings, and invalidate output when rendering settings
change. Do not invent author, timestamp, label, source-page URL or incoming URL contracts.

Acceptance: local links, moves, removals, selected-page runs, fragments, cross-space selection,
breadcrumb escaping, metadata quoting, settings changes, and idempotent subsequent syncs.

## Color and caption fidelity

Default to retaining observed font/background colors using constrained inline HTML, and retain
status title text alongside color. Reject arbitrary CSS values and escape HTML-sensitive text.
Do not infer captions from filenames or alt text. Confirm source shapes before adding recognition.

The supplied page has now confirmed `textStyle.attrs.color/backgroundColor` with RGB, hexadecimal,
empty and null values, and the human route documented in `compatibility.md`. Images expose `title`
but had no populated titles. Display explicit image titles as captions, without claiming support
for any unobserved separate caption node. Background color serialization uses the confirmed field
and the same literal color grammar. Status literal colors use their CSS meaning, not an assumed
product palette. Live access was limited to read-only source inspection; regression tests use
independently authored neutral examples.

Acceptance: safe colored text and status labels, mixed formatting, literal HTML characters,
invalid colors, explicit captions, and unchanged plain-Markdown passthrough. Add sanitized HTTP
contract fixtures for newly confirmed source shapes and document them in `compatibility.md`.

## Compatibility, verification and rollback

Keep command names, exit codes and JSON result schema. Advance the renderer version when output
changes so existing mirrors refresh. Preserve attachment reuse, checkpoint fingerprinting and
atomic output replacement. Any new output settings must participate in incremental invalidation.

The implementing agent owns production code, focused regression tests and README/compatibility
updates. First observe failing behavior tests, implement the smallest change and run focused
tests, then lint, formatting, the full coverage suite and both CLI invocation smoke tests.
Package publishing and releases are outside this change. Rollback is to run the previous package and refresh affected pages;
new manifest metadata must remain optional to older readers.

## Implemented synchronization details

`navigation.py` owns route and local-path identity lookup. The exporter builds maps from the old
and staged manifests, then repairs inline Markdown page links after every selection has finished.
Freshly rendered pages use current relative paths; reused/moved pages resolve their old relative
links against their previous path. Unselected managed referring pages may receive local repairs,
without a remote content request, and count as updated in the existing result schema.

The pass skips front matter, inline code and fenced code handled by the Markdown destination
scanner. It writes changed files atomically, including files initially hard-linked to the old
mirror. Stable path maps and a per-page navigation version allow unchanged Markdown to skip the
scan. Checkpoints saved before navigation lack that version and are processed on recovery.

Renderer version 7 refreshes 0.4.0 pages once. Optional manifest fields store ancestor context,
render-setting and breadcrumb hashes, and the navigation version; no raw page body or source URL
is added to the manifest. The existing optional YAML adds source URL, space name and ancestor/
parent context. Authors, dates and tags remain unsupported pending observed fields.

## Verification

Completed locally on Python 3.12.11: `uv sync --dev`, `uv run ruff check .`,
`uv run ruff format --check .`, and
`uv run pytest --cov=gitee_wiki_markdown_exporter --cov-report=term-missing` (147 passed, 83% overall
coverage). Both `uv run gw-export --help` and
`uv run python -m gitee_wiki_markdown_exporter --help` succeeded. `git diff --check` found no
whitespace errors. Focused tests first demonstrated missing retries, color output and local links;
an additional failing regression exposed nested fenced-code examples being rewritten, then passed
after the scanner fix. Link-repair rollback and first-sync checkpoint recovery are covered.

All automated tests used neutral synthetic content. Separately, a read-only manual export completed
with no resource errors and the visual result was confirmed. Its Markdown, assets and HTML preview
were kept outside the repository; no live content was copied into fixtures or documentation.
No package version, dependency, release or publishing configuration changed, so packaging/release
checks were not run.
