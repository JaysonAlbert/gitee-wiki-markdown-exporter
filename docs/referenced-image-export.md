# Current-body image export

Scope: enable current-body image selection by default using the existing reference analysis in
[architecture.md](architecture.md#reference-aware-diagnostics). This is an L1 incremental change
across configuration, exporter selection and incremental reuse. The implementation owner is the
current repository task; no remote endpoint or response contract changes.

## Behavior and acceptance

- `export.only_referenced_images` is a boolean, defaulting to true.
- True excludes only listed images proven unreferenced by the latest body, before download.
  Referenced and unknown images, explicit attachment components and non-image files remain.
- Image identification shares the existing name/MIME detection with payload validation, and
  also considers the attachment URL's extension. Body-only resources and diagrams remain intact.
- Enabling the setting refreshes selected pages and removes excluded previously managed images
  transactionally, preserving local untracked files and unselected pages.
- An unchanged filtered export skips body and byte downloads while still polling metadata.
  Changed revisions or any listed metadata, including excluded items, trigger reclassification.
- Disabled mode restores archived attachments. Existing configurations that omit the key also
  adopt filtering; set false explicitly to retain archival behavior. Failed transactions leave the old mirror intact;
  interrupted first syncs cannot resume a checkpoint under a different selection policy.

## Boundaries and risks

This does not prove that an unused image belongs to a historical revision, deduplicate images,
delete remote resources, remove non-images, or change the published package version. Unknown
syntax retains images deliberately; current-body filtering is not a strict unused-file oracle
for unsupported formats. Cleanup uses only managed paths for selected pages. Selection changes
also work with stale-page cleanup disabled. Revert the boolean to restore full archival.

## Verification

Synthetic exporter regressions cover filtering, retained references, unknown syntax, metadata
changes, incremental skipping, migration/rollback, failure atomicity, and checkpoint policy
changes. Configuration tests cover loading, safe rendering and boolean validation. Run the full
local test suite, Ruff and both CLI help forms; no live Wiki tests or publication are required.
