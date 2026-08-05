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

Install the locked development environment:

```bash
make install
```

Start Colima's open-source runtime, then run the complete local platform and
deterministic acceptance slice:

```bash
export EEG_POSTGRES_PASSWORD='replace-with-a-local-password' # pragma: allowlist secret
colima start --cpu 8 --memory 24 --disk 200
make local-up
make local-smoke
uv run eeg rebuild-search
uv run eeg search "monetary policy inflation"
uv run eeg readiness
curl --fail http://127.0.0.1:8000/health/ready
```

The local runtime uses only open-source components and does not require a cloud
account or proprietary model API. See the
[local platform guide](docs/how-to/local-development.md) for prerequisites,
host-mode development, and safe shutdown.

Run all repository checks before sharing a change:

```bash
make check
```

## Documentation

The documentation source lives under [`docs/`](docs/). The MkDocs site builds in
strict mode and generates API reference pages from package docstrings.

| Start here | Use it for |
| --- | --- |
| [Local platform](docs/how-to/local-development.md) | Run PostgreSQL, OpenSearch, the API, and the acceptance slice. |
| [Step 1 foundation](docs/explanation/step-1-foundation.md) | Resolved pilot decisions, contracts, and implementation boundary. |
| [Step 2 corpus foundation](docs/explanation/step-2-corpus-foundation.md) | Governed connectors, parsing, quarantine, identity, and operator workflow. |
| [Step 3 search foundation](docs/explanation/step-3-search-foundation.md) | Hybrid retrieval, strict historical cutoffs, version collapse, provenance, and the local workbench. |
| [Step 4 projects and evidence](docs/explanation/step-4-projects-and-evidence.md) | Collaborative screening, anchored comparison tables, review, policy links, and reproducible exports. |
| [Step 5 clients and readiness](docs/explanation/step-5-clients-and-readiness.md) | Python/R clients, eight agent tools, audit, readiness, promotion, and deferred production controls. |
| [Client and agent-tool guide](docs/how-to/clients-and-agent-tools.md) | Use the versioned API from Python, R, or an approved tool consumer. |
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
