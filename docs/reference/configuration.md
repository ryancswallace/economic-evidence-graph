# Configuration reference

## Runtime environment

The initial composition accepts only `EEG_PROFILE=local`. It fails at startup
rather than substituting an in-memory or SQLite provider.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `EEG_PROFILE` | No | `local` | Deployment composition. Only `local` is implemented. |
| `EEG_DATABASE_URL` | Yes | None | SQLAlchemy URL using the `postgresql+psycopg` driver. |
| `EEG_ARTIFACT_ROOT` | No | `.eeg/artifacts` | Root of the content-addressed filesystem store. |
| `EEG_OPENSEARCH_URL` | No | `http://127.0.0.1:9200` | Active Step 3 OpenSearch provider endpoint. |
| `EEG_API_HOST` | No | `127.0.0.1` | HTTP bind host. |
| `EEG_API_PORT` | No | `8000` | HTTP bind port from 1 through 65,535. |
| `EEG_CRAWLER_EMAIL` | No | Project contact | Contact included in the governed crawler user agent and Crossref polite-pool request. |
| `EEG_CORE_API_KEY` | CORE only | None | CORE API bearer credential. Its value is never included in configuration output. |
| `EEG_SOURCE_REGISTRY` | No | `config/source-registry.toml` | Versioned source-policy registry path. |
| `EEG_REQUEST_TIMEOUT_SECONDS` | No | `30` | Bounded source request timeout. |
| `EEG_MINIMUM_FREE_SPACE_GIB` | No | `20` | Required free space for the local readiness gate. |

`eeg show-config` prints the effective configuration with database passwords
redacted.

## Versioned pilot contracts

Files under `config/` are repository-controlled inputs:

- `source-registry.toml` selects the five internally approved pilot source
  families. It permits governed internal retrieval and processing while
  denying full-text display, export, and redistribution.
- `source-cards.toml` records scope, entry points, access methods, robots
  applicability, and known source limitations.
- `temporal-policy.toml` fixes strict cutoff semantics.
- `local.toml` fixes initial resource budgets and capacity gates.
- `benchmark-plan.json` fixes benchmark strata and split policy.
- `schemas/` fixes portable benchmark and evidence payloads.

Runtime secrets do not belong in these files, images, manifests, logs, or Git.

## Search configuration identities

Search mappings, reciprocal-rank weights, and the deterministic embedding
algorithm are code-versioned constants. Every activated generation persists
their IDs with the corpus manifest. Changing a mapping, ranking configuration,
embedding dimension, or algorithm requires a new immutable generation; it is
never applied silently to an existing index.
