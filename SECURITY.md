# Security policy

Please report credential exposure, path traversal, unsafe cleanup, or authentication-boundary
issues privately through GitHub Security Advisories for this repository.

Do not open a public issue containing a bearer token, tenant identifier, private hostname, page
body, raw API response, or attachment URL. Revoke any credential that may have been exposed before
submitting a report.

The exporter performs only read-only remote operations: it uses `GET` for Wiki content and a
non-mutating `POST /api/wiki/attachments/list` query for attachment metadata. It rejects off-origin
attachment URLs and does not follow HTTP redirects. The draw.io renderer loads the configured
Gitee-hosted preview application, then blocks cross-origin browser requests before injecting
component XML.

Local cleanup is limited to files recorded in the exporter's manifest, but users should still run
the first synchronization against a new output directory.
