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

Create `app_data.json` and point `GWME_CONFIG_PATH` at it:

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

Complete-space incremental runs still expand the full lazy-loaded tree and poll the latest revision
and attachment metadata for every selected page. Previously recorded diagrams add one component XML
request each. Network request volume therefore grows with the number of pages, paginated attachment
lists, and diagrams even when no page bodies or attachment bytes need downloading.

Skipped attachments are not recorded as successfully downloaded in `gitee-wiki-lock.json`. Human
output prints a warning for each skipped attachment; `--json` includes the same sanitized messages
in `errors`. A partial export still exits `0` because the requested Markdown mirror was committed,
while `status: "partial"` lets automation distinguish it from a complete `status: "ok"` run.

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
