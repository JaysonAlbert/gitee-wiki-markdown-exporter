# Compatibility and API contracts

Gitee Project Wiki is distinct from the Git-backed repository Wiki available on gitee.com. If a
Wiki exposes a clone URL ending in `.wiki.git`, use Git instead of this exporter.

This exporter targets the gateway contract observed on a self-hosted Gitee installation:

| Capability | Method and path | Required response |
| --- | --- | --- |
| Resolve space | `GET /api/wiki/spaces/key/{key}` | `data.id`, `data.key` |
| Read tree | `GET /api/wiki/spaces/{id}/tree` | `data.tree[]` with page IDs |
| Latest revision | `GET /api/wiki/spaces/{id}/pages/{page}/history` | first `data.items[]` or `data.list[]` ID |
| Revision body | `GET /api/wiki/spaces/{id}/pages/{page}/history/{revision}` | string `data.content` containing Markdown/plain text or the observed JSON document |
| Attachments | `POST /api/wiki/attachments/list` | `data.list[]` |
| Attachment bytes | URL under `/wiki-static/` | bounded binary response |

History pagination uses one-based `offset=1`, matching the observed gateway behavior.

Some installations label a revision as `text` while `data.content` is a JSON object whose
`default` property contains a ProseMirror-style `doc`. The exporter recognizes that exact envelope
and renders common headings, paragraphs, marks and links, lists, blockquotes, code blocks, layout
containers, and tables as Markdown. Invalid or unrecognized JSON remains plain text rather than
being guessed at. Binary YDoc state is not supported.

The exporter accepts envelopes where `data` contains the resource. An explicit non-zero `code` or
`success: false` is an error. HTTP errors are reported with method, sanitized URL, and status only.

## Reporting another contract

Open an issue with:

1. Gitee edition and version, if known;
2. the command used;
3. endpoint path and HTTP status;
4. a sanitized response shape with tokens, tenant IDs, usernames, titles, bodies, and attachment
   URLs removed.

Never publish a production token or an unsanitized response payload.
