# Client and agent-tool contracts

## Compatibility identities

| Contract | Identity | Source |
| --- | --- | --- |
| REST resources and problems | `eeg.api.v1` | `clients/openapi/eeg-v1.json` |
| Agent-tool envelope | `eeg.agent-tools.v1` | `GET /v1/agent-tools` |
| Project evidence export | `eeg.project-evidence.v1` | Export data and manifest |
| Promotion manifest | `eeg.promotion-manifest.v1` | `eeg promotion-manifest` |

All API request models reject extra fields. HTTP problems use
`application/problem+json` and include a stable code, request ID, retryable flag,
and permission-safe context.

## Tool bounds

| Input | Bound |
| --- | --- |
| Search result limit | 1-20 |
| Version comparison result limit | 1-100 |
| Relationship depth | 1-3 |
| Relationship node limit | 1-100 |
| Relationship edge limit | 1-200 |
| Work, version, evidence, project, and table identifiers | 1-64 characters |
| Idempotency key | 1-255 characters |

`trace_citations` retains only direct citations. `trace_policy_links` preserves
direct citation, explicit mention, shared source, and reviewed topical
suggestion as separate edge types. Both are bounded, bidirectional adjacency
lookups and neither asserts causal influence.

## Audit record

`tool_invocation` stores the invocation ID, actor, enumerated tool and outcome,
argument digest, optional result digest, opaque target IDs, nonnegative duration,
UTC timestamp, and whole-record digest. The application does not store query or
evidence content in this table. The migration and repository use an append-only,
idempotent insert contract.

## Client support boundary

The Python client supports Python 3.11-3.14 and is exported as `EegClient`. The R
client declares R 4.2 or later. The initial clients target the local v1 API;
future authentication can be added at the transport boundary without changing
research resource schemas.
