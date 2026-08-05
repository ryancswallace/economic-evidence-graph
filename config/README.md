# Versioned pilot contracts

This directory contains inputs fixed during discovery for the local pilot. They
are reviewed configuration, not implicit defaults in connector code.

| File | Contract |
| --- | --- |
| `source-registry.toml` | Five selected source families and default-deny acquisition/delivery actions. |
| `source-cards.toml` | Reviewed scope, entry points, access method, and known limitations for each source. |
| `temporal-policy.toml` | Strict historical-cutoff date precedence and safety classes. |
| `local.toml` | Resource envelope for the 48 GiB Apple Silicon reference host. |
| `benchmark-plan.json` | Sixty-task benchmark stratification and split policy. |
| `schemas/benchmark-task-v1.schema.json` | Annotation and adjudication contract for each benchmark task. |
| `schemas/evidence-record-v1.schema.json` | Portable evidence-record exchange contract. |

The five real source families are enabled only for the approved internal pilot.
Unknown rights allow internal retrieval, storage, extraction, and embedding but
never full-text display, export, or redistribution. An absent action is denied.
