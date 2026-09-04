# Changelog

All notable changes to this project will be documented in this file.

## 0.3.0 - 2026-09-04

- Expand Gitee's lazy-loaded page tree recursively so complete-space exports include every
  descendant page.
- Mirror draw.io components as sanitized local SVG files, including multi-page diagrams, without
  persisting editable source XML.
- Detect diagram-only changes by polling component content hashes even when the host page revision
  is unchanged, and preserve the last successful SVG across transient failures.
- Detect attachment replacements using Gitee's upload timestamp in addition to existing metadata.
- Bound concurrent tree expansion and per-page state reads to make large incremental mirrors
  practical without changing deterministic output ordering.

## 0.2.0 - 2026-09-04

- Refactor Gitee ProseMirror rendering around schema-driven node and mark registries.
- Escape Markdown-significant text and link delimiters, coalesce fragmented marks, and refresh
  mirrors produced by the previous renderer.
- Continue exporting Markdown when individual attachment downloads fail, report a partial result,
  and retry skipped attachments on later runs.
- Preserve attachment metadata across unchanged syncs so downloaded files remain reusable.

## 0.1.1 - 2026-09-03

- Render observed Gitee ProseMirror-style revision JSON as Markdown instead of exporting raw JSON.
- Refresh pages created by an older renderer once even when the Gitee revision is unchanged.

## 0.1.0 - 2026-09-03

- Initial incremental Gitee Project Wiki to Markdown exporter.
- Automated versioned publishing to PyPI through GitHub OIDC trusted publishing.
