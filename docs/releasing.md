# Releasing

The project uses semantic versions and publishes immutable releases to PyPI. Merges to `main`
run CI but do not publish a package.

## Version policy

- `PATCH` fixes bugs without intentionally changing the public CLI or configuration contract.
- `MINOR` adds backward-compatible commands, options, configuration, or export behavior.
- `MAJOR` may remove or incompatibly change a public contract.

Pre-release versions must also be valid Python package versions, such as `0.2.0rc1`.

## Release process

1. Update `project.version` in `pyproject.toml` and add the release notes to `CHANGELOG.md`.
2. Merge that change to `main` and wait for CI to pass.
3. Create a GitHub Release from the `main` commit with tag `v<project.version>`.
4. The `Publish to PyPI` workflow verifies the tag, builds and checks both distributions, and
   publishes them through PyPI Trusted Publishing.

PyPI does not allow an existing release file to be overwritten. If publishing fails after PyPI
accepts a version, fix the problem and release a new version instead of reusing the old tag.

## One-time PyPI setup

Configure a PyPI Trusted Publisher with these values:

| Field | Value |
| --- | --- |
| PyPI project name | `gitee-wiki-markdown-exporter` |
| GitHub owner | `JaysonAlbert` |
| GitHub repository | `gitee-wiki-markdown-exporter` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

The workflow uses GitHub OIDC and does not require a stored PyPI API token.
