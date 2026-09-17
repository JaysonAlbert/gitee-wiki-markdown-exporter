# gitee-wiki-markdown-exporter

Export Gitee **Project Wiki** spaces and pages to a local Markdown mirror. The first run
downloads the selected content; later runs expand the selected tree and compare page revisions,
attachment metadata, and recorded draw.io component hashes. They skip unchanged page bodies and
attachment bytes and avoid rerendering unchanged diagrams.

> [!IMPORTANT]
> This project targets the Gitee Project Wiki API exposed under `/api/wiki/`. It is different
> from repository wikis that can already be cloned as `<repository>.wiki.git`.

The command surface intentionally resembles
[`confluence-markdown-exporter`](https://github.com/Spenhouet/confluence-markdown-exporter):

```text
gitee-wiki-markdown-exporter pages --space SPACE PAGE_ID...
gitee-wiki-markdown-exporter pages-with-descendants --space SPACE PAGE_ID...
gitee-wiki-markdown-exporter spaces SPACE...
gitee-wiki-markdown-exporter sync
gitee-wiki-markdown-exporter config --show
```

The shorter `gw-export` alias provides the same commands.

## Status

This project is an early, contract-tested implementation based on API behavior observed on a
self-hosted Gitee Project Wiki installation. Gitee does not currently document these endpoints
as part of its public v5 OpenAPI. Revision bodies containing the observed ProseMirror-style JSON
document are converted to Markdown; already-Markdown bodies pass through unchanged. Test against
a non-production space before relying on it. The converter uses an explicit Gitee node/mark
registry inspired by the official ProseMirror Markdown serializer, so Gitee-specific schema names
remain isolated from Markdown escaping and rendering state. Embedded draw.io diagrams are rendered
locally to SVG and referenced from Markdown; the mirror does not retain editable `.drawio` files.

Font colors, text background highlights and literal status colors are retained with inline HTML.
For example, red text becomes `<span style="color: #ff0000;">Important note</span>`. Your Markdown
reader must allow HTML styles to display these colors; readers that strip styles keep the text.
Explicit image titles also appear as visible captions below their images. Empty titles and alt
text do not generate captions.

## Installation

Python 3.10 or newer is required. Pages containing draw.io diagrams also require a local Chrome,
Chromium, or Edge installation. Set `GWME_CHROME_PATH` when the browser executable is not in a
standard location.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install gitee-wiki-markdown-exporter
```

For local development:

```bash
git clone https://github.com/JaysonAlbert/gitee-wiki-markdown-exporter.git
cd gitee-wiki-markdown-exporter
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Installation does not create a configuration file, and there is no `config init` command.
Create `app_data.json` yourself and either place it at the default location below, set
`GWME_CONFIG_PATH`, or pass `--config-path` to a command. A minimal configuration for `sync` is:

```json
{
  "auth": {
    "gitee": {
      "url": "https://gitee.example.com",
      "tenant_id": "your-tenant"
    }
  },
  "export": {"output_path": "./wiki-export"},
  "sync": {"spaces": ["ENGINEERING"]}
}
```

Set `GITEE_PROJECT_WIKI_ACCESS_TOKEN` in the environment before exporting. The required values
are the Gitee base URL, tenant ID, output directory and a token (environment preferred).
`sync.spaces` is required for `sync`; `spaces` and `pages` take their targets as command arguments.
Relative output directories resolve against the configuration file's directory, not the shell's
working directory. `--output-path` overrides a valid configured output directory; it does not
remove the requirement to supply `export.output_path` in the file.

Everything else can use built-in defaults: current-body image filtering, incremental skipping,
stale-page cleanup, titles and breadcrumbs are enabled; YAML front matter is disabled; timeout
is 30 seconds, TLS certificate verification is enabled, and the per-attachment limit is 50 MiB.
Use `gw-export config --show --config-path ./app_data.json` to inspect resolved defaults with
the token redacted. Pages containing draw.io components also require an installed local browser.

The following example shows the common optional settings explicitly:

```json
{
  "auth": {
    "gitee": {
      "url": "https://gitee.example.com",
      "tenant_id": "your-tenant",
      "api_token_env": "GITEE_PROJECT_WIKI_ACCESS_TOKEN"
    }
  },
  "connection_config": {
    "timeout": 30,
    "verify_ssl": true
  },
  "export": {
    "output_path": "./wiki-export",
    "page_path": "{space_name}/{ancestor_titles}/{page_title}-{page_id}.md",
    "attachment_path": "{page_parent_path}/{page_title}/{attachment_file_id}{attachment_extension}",
    "diagram_path": "{page_parent_path}/{page_title}/diagram-{diagram_id}-{diagram_page}.svg",
    "include_document_title": true,
    "include_yaml_frontmatter": false,
    "include_page_breadcrumbs": true,
    "only_referenced_images": true,
    "skip_unchanged": true,
    "cleanup_stale": true,
    "lockfile_name": "gitee-wiki-lock.json",
    "max_attachment_bytes": 52428800
  },
  "sync": {
    "spaces": ["ENGINEERING"]
  }
}
```

The token is read from the environment named by `api_token_env`. You may instead use
`auth.gitee.api_token`, but an environment variable or secret manager is safer. Never commit the
configuration file when it contains credentials.

`include_page_breadcrumbs` defaults to `true`: child pages show their space, ancestors and current
title. Available managed ancestors are linked locally; unavailable ancestors remain plain text.
Set it to `false` for a mirror without breadcrumbs. Optional YAML front matter includes the
existing page/revision IDs and title, plus the space name, parent ID, ancestor IDs/titles and a
credential-free source-page URL. Author, date and label fields are not inferred from other values.

Default configuration locations are platform-specific:

- macOS: `~/Library/Application Support/gitee-wiki-markdown-exporter/app_data.json`
- Linux: `~/.config/gitee-wiki-markdown-exporter/app_data.json`
- Windows: the user application-data directory

## Usage

Export one or more complete spaces:

```bash
gw-export spaces ENGINEERING --output-path ./wiki-export
```

Export selected pages:

```bash
gw-export pages --space ENGINEERING 85455 85456
gw-export pages-with-descendants --space ENGINEERING 85455
```

Run the configured daily synchronization:

```bash
gw-export sync --json
```

`sync` honors `export.cleanup_stale` from the configuration. The `spaces` command enables stale
cleanup by default; pass `--no-cleanup-stale` for a one-off complete-space export that must not
remove previously managed pages.

A successful run exits `0`, a synchronization failure exits `1`, and a configuration or usage
error exits `2`. This makes `sync` suitable for cron, systemd timers, GitHub Actions, and Harness
scheduler adapters.

Example cron entry:

```cron
0 9 * * 1-5 /path/to/venv/bin/gw-export sync --json
```

## Incremental behavior

The exporter keeps `gitee-wiki-lock.json` in the output root. For every page it records the Gitee
page ID, current revision, Markdown renderer version, local path, tree parent, downloaded
attachments and embedded resources, and diagrams. A renderer upgrade refreshes the affected page
once even when its Gitee revision has not changed.

Same-origin, same-tenant inline page links using `/wiki/{tenant}/space/{spaceKey}/doc/{pageId}`
become relative links when their target is in the managed mirror. Labels and anchor fragments are
retained. Missing targets keep a query-free remote URL. After a page moves or is removed, the
exporter repairs incoming links in other managed pages, including unselected pages, without
fetching those pages' bodies. Such local repairs count as updated pages. Inline-code and fenced-code
examples and untracked files are left alone. Cross-space links resolve after all selections are staged.
If page paths and rendered content are unchanged, the link pass skips re-reading the Markdown.

Changes to title/front-matter/breadcrumb options or resource path templates also refresh affected
selected pages while reusing unchanged attachment bytes.

- unchanged revision, title, path, attachment metadata, and recorded diagram hashes: skip the page
  body and attachment bytes, and reuse existing SVG files;
- changed revision or attachment metadata: refresh the Markdown page while reusing unchanged
  attachment files;
- remaining `/wiki-static/` image or link destinations that are not present in attachment metadata:
  download them as managed embedded resources, rewrite successful downloads to local paths, and
  retry failed resources on the next synchronization;
- changed draw.io component hash: refresh the Markdown page and rerender that component as SVG even
  when the host-page revision is unchanged;
- failed attachment download: continue exporting the page, report the run as `partial`, preserve a
  query-free link to the remote attachment, and retry the attachment on the next synchronization;
- draw.io diagram: poll the component XML hash, render every changed diagram page locally as SVG,
  reference the SVG files from Markdown, and reuse unchanged SVGs;
- failed diagram fetch or render: preserve the last successful SVG when available, otherwise emit
  a visible placeholder, report the run as `partial`, and retry on the next synchronization;
- renamed or moved page: update its generated title/path and reuse unchanged attachments;
- Gitee Confluence redirect links: make root-relative destinations absolute so links still open
  outside the Gitee web application;
- deleted page during a complete-space sync: remove its managed local files when
  `cleanup_stale` is enabled;
- failure before the directory swap: keep the previous output directory and lockfile intact.

An interrupted first complete-space export to a new output directory automatically resumes on the
next run when the command targets the same output, spaces, provider identity, exporter version, and
export settings. Completed pages, downloaded attachments and embedded resources, and rendered
draw.io SVGs are reused after their current remote metadata or content hash is checked. The
incomplete mirror remains in a hidden checkpoint beside the output directory and is never exposed
as the live mirror.

Checkpoint recovery is automatic for `spaces` and configured `sync` runs, so there are no public
`--resume` or `--clear` options. A checkpoint with malformed metadata, missing files, or a different
target/configuration/version fingerprint is discarded before a fresh first export. Once the mirror
is committed, its checkpoint is removed. Concurrent commands for the same output directory fail
instead of sharing or modifying one another's staging data.

Attachment replacement detection uses the metadata returned by Gitee (ID, name, URL, advertised
size, content type, and upload timestamp). If a Gitee version replaces bytes without changing any
of those fields, the change cannot be detected without forcing a full download.

Only files recorded in the lockfile are eligible for cleanup.

Read-only API requests and resource downloads retry transient network failures and HTTP
408/429/500/502/503/504 up to three times, with 0.5/1/2-second backoff. A valid server `Retry-After`
overrides that delay up to 30 seconds. Interrupted downloads restart from byte zero with the same
size limit. Authentication failures, redirects, malformed responses and image/size validation
failures do not retry. If retries are exhausted, existing failure and partial-export behavior
applies.

Complete-space incremental runs still expand the full lazy-loaded tree and poll the latest revision
and attachment metadata for every selected page. Previously recorded diagrams add one component XML
request each. Network request volume therefore grows with the number of pages, paginated attachment
lists, and diagrams even when no page bodies or attachment bytes need downloading.

Skipped attachments are not recorded as successfully downloaded in `gitee-wiki-lock.json`. Human
output prints a warning for each skipped attachment; `--json` includes the same sanitized messages
in `errors`. A partial export still exits `0` because the requested Markdown mirror was committed,
while `status: "partial"` lets automation distinguish it from a complete `status: "ok"` run.

### Watching long synchronizations

Add `--progress` to `sync`, `spaces`, `pages`, or `pages-with-descendants` to see throttled progress
on stderr: checked page metadata, staged pages, downloaded resources, and recovered resources.
It can be combined with `--json`; stdout remains the final JSON result. Staged counts
are provisional until the `committed` event. On interruption, repeat the same command: completed
resource downloads can be reused, and the live mirror is only replaced after successful staging.

Images are checked before download success and cache reuse. If a server returns a login page or
another non-image body for an image, the result is partial and retains a remote link for retry.
These checks identify signatures and basic container structure, not every possible decoding error.

### Resource references and diagnostics

Attachment listing means page ownership, not current-body usage. By default, images confirmed
unreferenced in the current page body are omitted. Each attachment is classified as `referenced`,
`unreferenced`, or `unknown` from the current revision: image/link URLs and selected attachment-list
IDs establish references;
an attachment-list component without a selection displays all attachments. Unknown components or
unsupported body syntax cannot establish absence. Image node IDs are not attachment IDs.
These states do not describe references from other pages or historical revisions.

`export.only_referenced_images` defaults to `true` and omits listed image attachments confirmed
`unreferenced` in the latest page body. Image filenames, URL extensions and declared image MIME
types identify images. Referenced images, ordinary image links, selected attachment-list images,
and non-image attachments are retained. Unknown reference states are retained conservatively;
unsupported HTML/reference-link syntax or components may therefore keep extra images. Resources
embedded in the current body and rendered draw.io diagrams continue to be exported.

Set the option to `false` to archive all listed attachments. Upgrading an existing configuration
that omits this key enables filtering too; explicitly set it to `false` before upgrading to retain
full archival. Changing the effective policy refreshes selected pages once even when their
revisions are unchanged. Enabling it removes newly excluded images only from
those pages' previously managed files, inside the normal staging transaction; untracked files and
unselected pages are preserved. This selected-page reconciliation also applies when `cleanup_stale`
is false (that setting controls deletion of missing pages). Disabling the option restores full
attachment archival on the next sync. Subsequent unchanged runs still poll all attachment metadata
but do not refetch bodies or repeatedly attempt excluded images. Reference counts cover all listed
attachments, including intentionally excluded images; those exclusions are not download errors.
No remote attachment is deleted. This policy does not deduplicate images used by multiple pages.

JSON results retain `status`, `errors`, and exit codes and add `resourceIssues`,
`attachmentReferenceCounts`, and `contentStatus`. Each issue includes a page ID, resource kind,
reference state, safe reason code, retryability and, where available, a numeric resource ID or
an opaque resource key, HTTP status and byte limit. No response bodies, titles, credentials or
signed URLs are included. `contentStatus` describes resource completeness for selected current
pages: `partial` when a referenced resource failed, `unknown` when failed resources have uncertain
references, otherwise `complete`. A run can therefore have complete current content resources but
partial attachment archival. Legacy `status: partial` still reports every archival failure.

Transient HTTP/network failures already receive bounded transport retries; HTML responses,
file-type mismatches, malformed images, unsafe SVG declarations and size limits need distinct
operator actions. JPEG container validation accepts trailing data after a real end marker;
standard SVG 1.0/1.1 public declarations are accepted without fetching DTDs or expanding entities.
Actual truncated images, non-image bodies and entity declarations remain rejected.

## Compatibility

The following observed endpoints are used:

```text
GET  /api/wiki/spaces/key/{spaceKey}
GET  /api/wiki/spaces/{spaceId}/tree
GET  /api/wiki/spaces/{spaceId}/tree?parent={pageId}
GET  /api/wiki/spaces/{spaceId}/pages/{pageId}
GET  /api/wiki/spaces/{spaceId}/pages/{pageId}/history
GET  /api/wiki/spaces/{spaceId}/pages/{pageId}/history/{revisionId}
GET  /api/wiki/spaces/{spaceKey}/pages/{componentPageId}/component
POST /api/wiki/attachments/list
GET  /wiki-static/...
```

Requests send `Authorization: Bearer`, `X-Wiki-Tenant-Id`, and
`Api-Gateway-OAuth-Company`. See [Compatibility and API contracts](docs/compatibility.md) before
adding support for another Gitee release.

## License

MIT. This project borrows command and configuration concepts—not implementation code—from the
MIT-licensed `confluence-markdown-exporter` project. Its serializer architecture is informed by
the MIT-licensed official
[`prosemirror-markdown`](https://github.com/ProseMirror/prosemirror-markdown) project.

Maintainers can follow the [release process](docs/releasing.md) for versioning and automated PyPI
publishing.
