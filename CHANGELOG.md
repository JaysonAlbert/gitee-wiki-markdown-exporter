# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Refactor Gitee ProseMirror rendering around schema-driven node and mark registries.
- Escape Markdown-significant text and link delimiters, coalesce fragmented marks, and refresh
  mirrors produced by the previous renderer.
- Continue exporting Markdown when individual attachment downloads fail, report a partial result,
  and retry skipped attachments on later runs.

## 0.1.1 - 2026-09-03

- Render observed Gitee ProseMirror-style revision JSON as Markdown instead of exporting raw JSON.
- Refresh pages created by an older renderer once even when the Gitee revision is unchanged.

## 0.1.0 - 2026-09-03

- Initial incremental Gitee Project Wiki to Markdown exporter.
- Automated versioned publishing to PyPI through GitHub OIDC trusted publishing.
