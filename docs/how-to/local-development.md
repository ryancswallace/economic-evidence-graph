# Run the complete local platform

The local platform runs on macOS with open-source tooling. It does not require
Docker Desktop, a cloud account, or a proprietary model API. The reference host
remains the 48 GiB Apple Silicon MacBook Pro; an Intel Mac is suitable for
temporary development and the bounded connector smoke tests.

## Prerequisites

Install `uv`, Colima, the Docker-compatible CLI and Compose plugin, and `rsync`.
Start a Linux VM sized for the reference host:

```bash
colima start --cpu 8 --memory 24 --disk 200
make local-ready
```

Colima is the default documented path because it works on the temporary Intel
Mac. Podman remains usable by overriding `DOCKER` and `LOCAL_COMPOSE`; both
profiles run the same Compose specification.

## Start the complete composition

Choose a URL-safe local database password and keep it in the current shell:

```bash
export EEG_POSTGRES_PASSWORD='replace-with-a-local-password' # pragma: allowlist secret
make local-up
make local-status
```

The composition binds PostgreSQL, OpenSearch, and the API only to loopback. It
builds the application, applies packaged migrations, and waits for its data
services. Run the deterministic Step 1 acceptance slice with `make local-smoke`.

## Run corpus ingestion on the host

Copy `.env.example` to the ignored `.env`, populate `EEG_CORE_API_KEY`, and set
the database password. Do not place the key in shell history or command flags.

Start the data services and run a bounded live connector slice:

```bash
docker compose up --detach postgres opensearch
uv run eeg migrate
uv run eeg connector-smoke --source crossref
uv run eeg connector-smoke --source core
uv run eeg connector-smoke --source repec
uv run eeg connector-smoke --source boston_fed
uv run eeg connector-smoke --source board_fomc
```

To run the application inside the composition instead, keep its database and
artifact volume together and pass the ignored environment file only when CORE
needs it:

```bash
docker compose run --rm api eeg connector-smoke --source crossref
docker compose run --rm --env-from-file .env api eeg connector-smoke --source core
```

Use `--cursor` with the returned `next_cursor` to resume. `ingest-source`
defaults to 25 records and accepts at most 100 per invocation. The approved
pilot ceiling is 1,000 source records or 10 GiB of raw source data. Full text
with no explicit open license is stored as `rights_unknown`: it may be parsed
internally but cannot be displayed, exported, or redistributed.

Do not alternate host and container application commands against one database:
their artifact roots differ. Use one execution mode for a given local corpus.

## Build and search the local evidence index

After ingesting or suppressing source records, rebuild both immutable indexes
and atomically activate their aliases:

```bash
uv run eeg rebuild-search
uv run eeg search "monetary policy inflation"
uv run eeg search --as-of 2020-03-15T00:00:00Z \
  --source board_fomc "forward guidance expectations"
```

`--as-of` accepts only an aware UTC timestamp. Add `--save-run` to persist the
exact request and response. Use repeated `--source` or `--mode` options to
combine filters and channels. The default channels are lexical and the
deterministic T1 semantic baseline.

Run `uv run eeg serve` and open `http://127.0.0.1:8000/` for the local search,
work-record, and evidence-reader workspace. The OpenAPI interface remains at
`/docs`.

After migrations and an index build, run the Step 5 readiness gate:

```bash
uv run eeg readiness
```

It checks PostgreSQL, the artifact store, OpenSearch, the packaged migration
head, the active immutable index generation, and configured disk headroom. It
does not claim enterprise authentication, high availability, backup targets, or
AWS production acceptance.

## Build a reviewed evidence project

Open `http://127.0.0.1:8000/projects` for the Step 4 project workspace. The
default local actor is `local-user`; enter a different stable actor identifier
to exercise collaboration. Local actors provide pilot attribution and
authorization, not enterprise authentication.

The reproducible handoff sequence is:

1. Save a search run from the search workbench.
2. Create a project and attach the exact run ID.
3. Screen works using include, maybe, or an exclusion reason.
4. Create evidence fields from exact evidence-unit quotes and declare a table
   schema before adding comparison rows.
5. Add a reviewer and record approved, rejected, uncertain, or superseded
   review events.
6. Record research-to-policy relationships using their explicit relationship
   type, then export a reviewed table as JSON, CSV, or Markdown.

Every mutation uses the revision returned by the prior response. A concurrent
write returns HTTP 409; reload the project handoff before intentionally applying
a compensating decision. Preserve each export manifest with its data artifact.

Use the [client and agent-tool guide](clients-and-agent-tools.md) for Python, R,
and typed-tool examples. Use the
[local operations runbook](../runbooks/local-operations.md) before a pilot
handoff or promotion exercise.

## Stop safely

```bash
make local-down
```

This preserves named database, index, and artifact volumes. Removing them is a
separate destructive operation and is not part of a Make target. Run
`make check` before sharing a change.
