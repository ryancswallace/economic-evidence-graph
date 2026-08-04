# Release

Prepare release metadata with:

```bash
ECONOMIC_EVIDENCE_GRAPH_RELEASE_VERSION=0.2.0 make prepare-release
```

Validate locally with:

```bash
make check
```

Create a release tag after the release pull request merges:

```bash
ECONOMIC_EVIDENCE_GRAPH_RELEASE_VERSION=0.2.0 make release-tag
```
