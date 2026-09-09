# Confluence exporter comparison

Reviewed on 2026-09-09 against Gitee exporter `a6c4906` (0.4.0) and
`confluence-markdown-exporter` commit
[`74dc982`](https://github.com/Spenhouet/confluence-markdown-exporter/tree/74dc982b008321005e8ccecceb79d7e9a5fe2b8d).
The table records gaps at that baseline. The high/medium-priority implementation is described in
[Export usability improvements](export-usability-design.md); lower-priority items remain proposals.

## Requested change: preserve font colors

Confluence converts recognized font-color spans to `<font style="color: #rrggbb;">` and
background highlights to `<mark style="background: #rrggbb;">`. Both conversions are enabled by
default. See its [color converters](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/confluence_markdown_exporter/confluence.py#L1830)
and [configuration](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/confluence_markdown_exporter/utils/app_data_store.py#L646).

The baseline lacked font-color and background-highlight converters. Read-only inspection of the
subsequently supplied page confirmed `textStyle.attrs.color/backgroundColor`, with RGB and
hexadecimal values. The implementation uses this structure and independently authored neutral
test data. No page body, real identifiers or attachment metadata were copied into this repository.

Output for a confirmed red text mark:

```html
<span style="color: #ff0000;">Important note</span>
```

Use a standard inline `span` rather than copying the legacy `font` element. Preserve colors by
default without adding configuration unless a concrete need for plain output arises. The Markdown
reader must allow inline HTML and CSS to display the color. Readers that remove styling retain
the text but cannot promise the visual emphasis.

Implementation and acceptance boundaries:

- Register only the mark/attributes demonstrated by the Gitee sample. Add the sanitized envelope
  to an HTTP contract test and document it in `compatibility.md` before production code changes.
- Normalize supported literal colors to a constrained representation. Never copy arbitrary CSS,
  markup, or unknown attributes into generated HTML. Invalid colors retain the text without color.
- Cover red text, other observed colors, adjacent marked fragments, mixed bold/italic/link marks,
  Chinese text, inline code, headings, lists, tables, and characters requiring HTML escaping.
  Existing Markdown bodies must still pass through unchanged.
- Increment the Markdown renderer version so an existing mirror refreshes even when the remote
  page revision is unchanged. Prove refresh followed by a subsequent no-change skip through the
  exporter boundary, retaining unchanged attachment bytes.
- Update README with output behavior and reader limitations. Verify the regression first fails
  for missing color output, then run focused tests, lint/format checks and the full test suite.

## Features worth borrowing

| Priority | Capability | Gitee 0.4.0 behavior | Recommended scope and acceptance |
| --- | --- | --- | --- |
| High | Local page links | Localizes attachments and diagrams; Confluence redirect links become absolute remote links. No general page-ID-to-local-path link mapping. | Build a mapping for exported pages, retain link labels/fragments, and rewrite only verified page links. Test renamed targets with unchanged referring pages, cross-space selection, and unexported targets. Keep a usable remote link when no local target exists. |
| High | Bounded HTTP retries | Timeouts and interrupted-run recovery exist; individual requests do not retry transient errors. | Retry transient failures with a limit, backoff, and bounded `Retry-After` handling. Apply explicitly to read-only calls, including attachment-list POST. Do not retry authentication or malformed response errors. Test retry exhaustion and restart partially read downloads safely. |
| Medium | Background highlights and colored status labels | Background marks are unhandled; status nodes retain their title in bold and discard their color. | Confirm Gitee color semantics separately; retain text plus a validated HTML highlight. Do not copy Confluence's color palette without evidence. Test simultaneous foreground/background styling rather than choosing only one. |
| Medium | Visible image captions | Keeps image alt/title in Markdown image syntax, without a separate caption line. | Add captions only when a distinct caption field is observed. Do not duplicate alt text or invent captions from attachment filenames. |
| Medium | Breadcrumbs and richer metadata | Optional YAML contains page ID, revision, space key, and title; ancestor titles determine paths. | Consider a stable source link and local breadcrumb navigation first. Add tags, authors, or dates only after their API contract is confirmed. Test moves, quoting, missing fields, and output-setting invalidation. |
| Lower | Attachment selection | Downloads listed attachments plus discovered embedded resources. | Add a referenced-only mode only if actual export size warrants it. Preserve the existing default and handle attachment-list nodes, indirect references, and cleanup ownership explicitly. |
| Lower | Comments in sidecar files | No comment endpoint or export model. | Useful for archival completeness, but requires a separately observed read-only API, selection rules, privacy review of output, and incremental behavior independent of page revisions. |
| Conditional | Reader-specific links and metadata | Emits portable Markdown paths and basic YAML. | Add Obsidian wiki links or specialized metadata only for a requested target. Avoid imposing target-specific plugins on ordinary Markdown readers. |

Upstream evidence: [page-link conversion](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/confluence_markdown_exporter/confluence.py#L2250),
[retry client configuration](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/confluence_markdown_exporter/api_clients.py#L181),
[feature list](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/docs/features.md),
and [export options](https://github.com/Spenhouet/confluence-markdown-exporter/blob/74dc982b008321005e8ccecceb79d7e9a5fe2b8d/docs/configuration/options.md).
Gitee findings come from `rich_text.py`, `client.py`, `exporter.py`, and `config.py` at the baseline
above. The acceptance conditions and prioritization are this project's analysis.

## Existing strengths and boundaries

Gitee already supports task lists, alert blocks, a document directory, attachment-list nodes,
basic tables, draw.io SVGs, incremental synchronization, managed cleanup, progress reporting,
initial-export checkpoints, and interrupted resource-download recovery. These are not missing
features. Table spans currently preserve column positions by expanding a rectangular grid;
faithful merged-cell display would require a separate HTML-table design.

Confluence-specific Include/Excerpt, Page Properties Report, Jira enrichment, and third-party
macros need an actual Gitee counterpart before implementation is justified. Retain Gitee's
SVG-only draw.io policy, explicit space selection, transactional mirror replacement, same-origin
bounded downloads, and external scheduling. Do not copy Confluence behavior where it conflicts
with these contracts.

The implemented high/medium items cover font/background colors, literal status colors, visible
image titles, local page links, bounded retries, breadcrumbs and existing-field metadata. The
inspected images had empty titles; populated image-title captions are verified synthetically.
No separate caption schema, author/date/tag API, or Confluence-specific status palette is claimed.
