# Compatibility and API contracts

Gitee Project Wiki is distinct from the Git-backed repository Wiki available on gitee.com. If a
Wiki exposes a clone URL ending in `.wiki.git`, use Git instead of this exporter.

This exporter targets the gateway contract observed on a self-hosted Gitee installation:

| Capability | Method and path | Required response |
| --- | --- | --- |
| Resolve space | `GET /api/wiki/spaces/key/{key}` | `data.id`, `data.key` |
| Read tree | `GET /api/wiki/spaces/{id}/tree[?parent={page}]` | `data.tree[]` with page IDs and `isLeaf`; non-leaf nodes are expanded recursively |
| Resolve page | `GET /api/wiki/spaces/{id}/pages/{page}` | page title, parent, and optional `pagePathList[]` breadcrumbs |
| Latest revision | `GET /api/wiki/spaces/{id}/pages/{page}/history` | first `data.items[]` or `data.list[]` ID |
| Revision body | `GET /api/wiki/spaces/{id}/pages/{page}/history/{revision}` | string `data.content` containing Markdown/plain text or the observed JSON document |
| Diagram component | `GET /api/wiki/spaces/{key}/pages/{componentPage}/component` | string `data.content` containing draw.io/mxGraph XML |
| Attachments | `POST /api/wiki/attachments/list` | `data.list[]` with `id`/`attachId`, `name`/`filename`, and `url`; optional `size`, `type`/`contentType`, and `uploadAt`/`updatedAt` |
| Attachment bytes | URL under `/wiki-static/` | bounded binary response |

History pagination uses one-based `offset=1`, matching the observed gateway behavior. The root
tree response may contain only top-level nodes; child lookup uses the `parent` query parameter.
Attachment listing is a read-only query despite using `POST`; it paginates with `pageIndex`,
`pageSize`, and `offset` until the reported `total` is reached.

Some installations label a revision as `text` while `data.content` is a JSON object whose
`default` property contains a ProseMirror-style `doc`. The exporter recognizes that exact envelope
and renders common headings, paragraphs, marks and links, lists, blockquotes, code blocks, layout
containers, and tables as Markdown. Markdown-significant characters in text and link targets are
escaped, adjacent text fragments with the same marks are serialized as one marked span, and code
fences expand when their content contains backticks. Unknown container nodes retain recognized
descendants. Invalid or unrecognized top-level JSON remains plain text rather than being guessed
at. Binary YDoc state is not supported.

An observed `diagram` node stores a component page ID rather than a stable SVG URL. The exporter
fetches that component's draw.io XML and uses a local headless Chrome-compatible browser with the
Gitee-hosted preview application to produce portable SVG files. It writes the SVG files referenced
by Markdown and deliberately does not retain `.drawio` source files. Diagram rendering is invoked
only for pages that contain diagram nodes. A transient fetch or render failure preserves the last
successful SVG when available, marks the run partial, and retries during the next incremental sync.

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
