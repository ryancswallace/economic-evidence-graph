# Database migration runbook

Alembic revisions are packaged with the application. PostgreSQL is the canonical
database; SQLite is used only to exercise migration and repository contracts in
isolated tests.

## Apply migrations

Set a PostgreSQL URL and inspect the redacted configuration before upgrading:

```bash
export EEG_PROFILE=local
export EEG_POSTGRES_PASSWORD='replace-with-a-local-password' # pragma: allowlist secret
export EEG_DATABASE_URL="postgresql+psycopg://eeg:${EEG_POSTGRES_PASSWORD}@127.0.0.1:5432/eeg"
eeg show-config
eeg migrate
```

The local composition runs this operation in its `migrate` service before the
API starts.

## Change the schema

1. Change the SQLAlchemy metadata and affected repository mappings.
2. Add a new immutable revision under
   `src/economic_evidence_graph/migrations/versions/`.
3. Test upgrade from an empty database and from the preceding revision.
4. Add repository round-trip and API/export regression tests.
5. Run `make check`.

Never edit a released migration. Prefer a forward fix. A downgrade path may be
documented for a reversible development migration, but corpus records and
artifacts must not be silently destroyed.

## Failure handling

If a local upgrade fails, preserve the database and migration output. Inspect
the current `alembic_version`, fix the new revision or add a forward-fix
revision, and retry on a copy. Do not delete a shared or valued local corpus as a
recovery shortcut.
