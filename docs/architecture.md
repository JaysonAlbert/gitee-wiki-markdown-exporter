# Architecture

## Goal

Maintain a deterministic local Markdown mirror of selected Gitee Project Wiki content. A repeated
run must transfer only changed content, reconcile tree moves and deletions, and never expose the
Wiki token in output or exceptions.

## Boundaries

```text
CLI
  -> configuration
  -> export service
       -> Gitee Project Wiki client
       -> path renderer
       -> revision manifest
       -> transactional output directory
```

- `client` owns HTTP authentication, endpoint paths, response validation, pagination, and secret
  redaction.
- `exporter` owns selection, revision comparison, attachment localization, stale-file decisions,
  and run summaries.
- `rich_text` separates envelope detection from Markdown serialization. A serializer registry maps
  observed Gitee node and mark names to handlers, while a per-document state owns CommonMark
  escaping and nested rendering. Unknown container nodes preserve their supported descendants;
  unrecognized or already-Markdown top-level text still passes through verbatim.
- `diagram` owns local headless-browser rendering of Gitee draw.io component XML. It loads the
  Gitee-hosted preview application, blocks subsequent cross-origin network requests before
  injecting diagram content, and returns normalized SVG without persisting editable XML.
- `paths` owns cross-platform safe filenames and template expansion.
- `manifest` owns the versioned on-disk synchronization contract.
- `cli` performs parsing and rendering only; it does not contain synchronization rules.

## Synchronization algorithm

1. Resolve each requested space. Resolve explicit page-only selections directly from page metadata;
   for complete spaces or descendant selections, read the page tree and expand non-leaf nodes
   through the endpoint's `parent` query until the selected hierarchy is complete.
2. Flatten selected roots into page records with stable page IDs and ancestor titles.
3. Read the latest revision ID and attachment metadata for each selected page through a bounded
   worker pool while preserving deterministic result ordering. Poll component XML hashes for
   diagrams already recorded in the manifest, because a component may change without a new
   host-page revision.
4. Compare `(page ID, revision, renderer version, title, rendered path, attachment metadata,
   diagram state)` with the previous manifest.
5. Build a staging output beside the current output, initially populated from the previous mirror.
   A first complete-space synchronization uses a stable, private checkpoint staging directory;
   other runs continue to use disposable random staging.
6. Download changed page bodies and attachment bytes into staging, reuse unchanged attachments,
   and move unchanged pages whose tree path changed. Fetch draw.io component XML and render every
   diagram page locally as SVG; only SVG output is persisted. If an attachment or diagram fails,
   keep exporting the page, preserve the last successful diagram SVG when available, record a
   partial-run error, and leave the item incomplete so the next synchronization retries it.
7. For complete-space selections, remove stale managed files when cleanup is enabled.
8. Write the next manifest and replace the output directory. Attachment and diagram failures are
   recoverable pre-swap errors; any other pre-swap failure preserves a valid first-sync checkpoint
   or discards disposable staging, while the previous mirror remains unchanged. Backup deletion
   after a committed swap is best-effort.

The manifest is an optimization and cleanup authority, not a remote source of truth. Gitee page
IDs and revisions remain authoritative.

## First-sync checkpoints

Checkpointing is intentionally limited to an initial complete-space synchronization: every
selection must cover a complete space and the output directory must not exist yet. An existing
directory without a synchronization manifest uses disposable staging so checkpoint recovery cannot
overwrite untracked files changed between attempts. This keeps established incremental semantics
unchanged. Recovery is automatic rather than a new CLI mode, so scheduled `sync` jobs recover after
interruption without operator-only flags and the published command surface remains compatible.

The checkpoint staging directory, fingerprint sidecar, and per-page state directory live beside the
configured output. They are private implementation artifacts and are never copied into the
committed mirror. A small page-state record is written atomically after each completed page and
after each successfully materialized attachment or draw.io component; the cumulative manifest is
not rewritten for every page. File data is committed before a state record refers to it, so a crash
may leave an unreferenced file but cannot make incomplete bytes reusable. Partial page entries
deliberately do not claim that the Markdown page is complete; they only authorize reuse of
validated attachment and diagram files on the next attempt.

A SHA-256 fingerprint binds a checkpoint to:

- the checkpoint schema, package version, and Markdown renderer version;
- the normalized provider/tenant identity without persisting credentials or the tenant value;
- the resolved output path and every output-affecting export setting; and
- the ordered synchronization selections, including spaces, page IDs, descendant mode, complete
  space mode, and stale-cleanup policy.

Resume reloads only a current-schema checkpoint whose sidecar, staging directory, per-page state,
fingerprint, reconstructed manifest, and referenced files validate. A missing component, malformed
or unsupported JSON, unsafe managed path, or fingerprint mismatch causes all managed checkpoint
artifacts to be removed before a fresh initial export. The live output is never consulted from an
incompatible checkpoint. If a formal output manifest now exists (for example, the process committed
the swap immediately before termination), the leftover checkpoint artifacts are stale and are
removed before the normal incremental run.

One non-blocking process lock is held for the output target across checkpoint preparation, remote
work, manifest writing, and the final swap. Operating-system lock release on process termination
makes crash recovery possible without a stale lease timeout. A concurrent invocation for the same
output fails before reading or mutating staging. Successful replacement is followed by best-effort
checkpoint artifact cleanup; the empty lock file may remain because deleting a lock path after
unlocking can race with a new process. Cleanup failure must not reverse or report failure for an
already committed mirror.

## Compatibility policy

The Project Wiki API is treated as a versioned external contract even though it is not part of the
public Gitee v5 OpenAPI. Parsing accepts only explicitly observed response variants. A missing field
or non-success envelope fails the run instead of silently producing incomplete Markdown.

New endpoint variants require a captured, sanitized fixture and a contract test. Write endpoints
are outside the exporter boundary.

The rich-text registry follows the extension model of the official
[`prosemirror-markdown`](https://github.com/ProseMirror/prosemirror-markdown) serializer without
depending on a JavaScript runtime. It is intentionally configured for the observed Gitee schema
(`bulletList`, `codeBlock`, `layoutRow`, and related names), rather than assuming that every
ProseMirror installation shares one JSON schema. Markdown-to-ProseMirror parsing and publishing
content back to Gitee remain outside the read-only exporter boundary.

## Security

- Prefer an environment variable for the bearer token.
- Never include authorization headers or raw tokens in logs, manifests, JSON output, or exceptions.
- Reject tree-derived paths that escape the configured output root.
- Bound attachment sizes before buffering them in memory.
- Reject off-origin attachment URLs and do not follow HTTP redirects.

## Non-goals

- publishing Markdown back to Gitee;
- migrating Confluence history;
- decoding binary collaborative YDoc state beyond the observed JSON document;
- exporting every tenant-visible space without an explicit allowlist;
- providing a daemon or scheduler—the `sync` command is the schedulable primitive.
