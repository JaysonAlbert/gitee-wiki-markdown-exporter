# gitee-wiki-markdown-exporter

Export Gitee **Project Wiki** spaces and pages to a local Markdown mirror. The first run
downloads the selected content; later runs compare page revisions and attachment metadata and
only download changed page bodies and attachment bytes.

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
as part of its public v5 OpenAPI. Test against a non-production space before relying on it.

## Installation

Python 3.10 or newer is required.

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

A successful run exits `0`, a synchronization failure exits `1`, and a configuration or usage
error exits `2`. This makes `sync` suitable for cron, systemd timers, GitHub Actions, and Harness
scheduler adapters.

Example cron entry:

```cron
0 9 * * 1-5 /path/to/venv/bin/gw-export sync --json
```

## Incremental behavior

The exporter keeps `gitee-wiki-lock.json` in the output root. For every page it records the Gitee
page ID, current revision, local path, tree parent, and downloaded attachments.

- unchanged revision, title, path, and attachment metadata: skip the page body and attachment
  bytes;
- changed revision or attachment metadata: refresh the Markdown page while reusing unchanged
  attachment files;
- renamed or moved page: update its generated title/path and reuse unchanged attachments;
- deleted page during a complete-space sync: remove its managed local files when
  `cleanup_stale` is enabled;
- failure before the directory swap: keep the previous output directory and lockfile intact.

Attachment replacement detection uses the metadata returned by Gitee (ID, name, URL, advertised
size, and content type). If a Gitee version replaces bytes without changing any of those fields,
the change cannot be detected without forcing a full download.

Only files recorded in the lockfile are eligible for cleanup.

## Compatibility

The following observed endpoints are used:

```text
GET  /api/wiki/spaces/key/{spaceKey}
GET  /api/wiki/spaces/{spaceId}/tree
GET  /api/wiki/spaces/{spaceId}/pages/{pageId}/history
GET  /api/wiki/spaces/{spaceId}/pages/{pageId}/history/{revisionId}
POST /api/wiki/attachments/list
GET  /wiki-static/...
```

Requests send `Authorization: Bearer`, `X-Wiki-Tenant-Id`, and
`Api-Gateway-OAuth-Company`. See [Compatibility and API contracts](docs/compatibility.md) before
adding support for another Gitee release.

## License

MIT. This project borrows command and configuration concepts—not implementation code—from the
MIT-licensed `confluence-markdown-exporter` project.

Maintainers can follow the [release process](docs/releasing.md) for versioning and automated PyPI
publishing.
