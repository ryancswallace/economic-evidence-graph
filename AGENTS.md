# Economic Evidence Graph Project Instructions

## Project

This repository contains `economic-evidence-graph`, a Python package generated from
the Copier project template.

## Package Layout

* `src/economic_evidence_graph/__init__.py`: public package exports.
* `src/economic_evidence_graph/_core.py`: core example functionality.
* `src/economic_evidence_graph/exceptions.py`: package-specific exceptions.
* `tests/`: tests.

## Tooling

Use uv. The authoritative local check command is:

```bash
make check
```

It checks the uv lockfile, Ruff, Markdown, the documentation site, GitHub
Actions workflows, CSpell, secrets, Bandit, deptry, pip-audit, pytest and
coverage, basedpyright, built distributions, and CycloneDX SBOM generation.
Run it after edits.

## Code Style

* Python 3.11+.
* Type hints should pass basedpyright in standard mode.
* Use Ruff for linting and formatting.
* Public functions/classes use Google-style docstrings.
* Private helpers use concise one-line PEP 257 docstrings.
* Keep metadata strict-JSON-safe when adding generated metadata.
