# Economic Evidence Graph

[![CI](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/ci.yml)
[![Documentation](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/docs.yml)
[![Docker](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/docker.yml/badge.svg?branch=main)](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/docker.yml)
[![CodeQL](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/scorecard.yml/badge.svg?branch=main)](https://github.com/ryancswallace/economic-evidence-graph/actions/workflows/scorecard.yml)
[![Python 3.11-3.14](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://github.com/ryancswallace/economic-evidence-graph/blob/main/pyproject.toml)
[![Typed with basedpyright](https://img.shields.io/badge/types-basedpyright-2f6fdd)](https://github.com/DetachHead/basedpyright)
[![Linted with Ruff](https://img.shields.io/badge/lint-Ruff-46a2f1)](https://docs.astral.sh/ruff/)
[![Coverage gate: 95%](https://img.shields.io/badge/coverage%20gate-%E2%89%A595%25-2e7d32)](https://github.com/ryancswallace/economic-evidence-graph/blob/main/pyproject.toml)
[![SBOM: CycloneDX 1.6](https://img.shields.io/badge/SBOM-CycloneDX%201.6-6f42c1)](https://cyclonedx.org/)

A versioned, auditable evidence layer for economic research and policy synthesis

## Quick Start

Install the package:

```bash
uv add economic-evidence-graph
```

Use it from Python:

```python
from economic_evidence_graph import greet

print(greet("Python"))
```

For local development from this repository:

```bash
make ready
```

## Documentation

The documentation source lives under [`docs/`](docs/). The MkDocs site builds in
strict mode and generates API reference pages from package docstrings.

| Start here | Use it for |
| --- | --- |
| [Development](docs/project/development.md) | Local setup, test commands, and repository layout. |
| [Tooling](docs/explanation/tooling.md) | The validation, release, and automation stack. |
| [API reference](docs/reference/index.md) | Generated package API pages. |
| [Release runbook](docs/runbooks/release.md) | Release metadata and publishing workflow. |

## Project Links

* [Contributing](CONTRIBUTING.md)
* [Changelog](CHANGELOG.md)
* [Security policy](SECURITY.md)
* [Release policy](RELEASE.md)
* [Code of conduct](CODE_OF_CONDUCT.md)
* [Citation metadata](CITATION.cff)

## License

Economic Evidence Graph is proprietary software. All rights are reserved; see the
[proprietary notice](LICENSE) for details.
