# Releasing

Economic Evidence Graph uses [Semantic Versioning](https://semver.org/). Release
metadata is prepared through scripts in `scripts/` and the `Makefile` targets.

## Prepare a Release

```bash
ECONOMIC_EVIDENCE_GRAPH_RELEASE_VERSION=0.2.0 make release-pr-ready
```

This validates the version, updates `pyproject.toml`, `CITATION.cff`,
`CHANGELOG.md`, and `uv.lock`, runs `make check`, and opens a release pull
request.

## Tag a Release

After the release pull request is merged:

```bash
ECONOMIC_EVIDENCE_GRAPH_RELEASE_VERSION=0.2.0 make release-tag
```

Pushing a `v*` tag builds release artifacts and drafts a GitHub Release.
