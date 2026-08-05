# Development

Use uv for all Python dependency management.

```bash
make ready
```

Run the full local validation suite before submitting changes:

```bash
make check
```

The package source lives in `src/economic_evidence_graph/`, tests live in `tests/`,
versioned pilot inputs live in `config/`, migrations live in the package under
`migrations/`, clients live in `clients/`, examples live in `examples/`, and
documentation lives in `docs/`.

The main dependency rule is inward: domain code is infrastructure-free,
application services depend on domain objects and protocols, and concrete
providers own PostgreSQL and filesystem integration. API and CLI modules compose
these layers.

For the complete local PostgreSQL and OpenSearch composition, follow the
[local platform guide](../how-to/local-development.md).

When a public schema changes intentionally, regenerate
`clients/openapi/eeg-v1.json` with `make openapi-contract`, inspect the diff, and
run `make check`. An accidental API change must not be accepted by merely
refreshing the contract.
