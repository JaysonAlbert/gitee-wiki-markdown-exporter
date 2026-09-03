# Security policy

Please report credential exposure, path traversal, unsafe cleanup, or authentication-boundary
issues privately through GitHub Security Advisories for this repository.

Do not open a public issue containing a bearer token, tenant identifier, private hostname, page
body, raw API response, or attachment URL. Revoke any credential that may have been exposed before
submitting a report.

The exporter only performs read requests. Cleanup is limited to files recorded in its local
manifest, but users should still run the first synchronization against a new output directory.
