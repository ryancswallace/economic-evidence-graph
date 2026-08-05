# Step 5 clients and local readiness

Step 5 completes the initial local implementation boundary. It adds stable
Python and R access, exactly eight bounded agent tools, privacy-safe invocation
audit, checked-in OpenAPI compatibility metadata, local readiness checks, and a
provider-neutral promotion manifest. AWS adapters and institutional production
acceptance remain deferred under the earlier local-first decision.

## Implemented contracts

### Python and R clients

The Python `EegClient` validates response models and structured problem details,
adds explicit actor attribution, follows opaque search cursors under a page cap,
waits for bounded asynchronous jobs, and supports governed project exports. The
R package under `clients/r/eegR` exposes the same research path using only the
open-source `httr2` dependency.

The API contract is checked in at `clients/openapi/eeg-v1.json`. Regeneration is
an explicit review operation, not an automatic way to accept compatibility
drift.

### Agent tools

| Tool | Purpose | Hard boundary |
| --- | --- | --- |
| `search_literature` | Explainable hybrid and historical search | At most 20 results per call |
| `get_work` | Canonical metadata and version projections | At most 50 versions |
| `retrieve_evidence` | Exact policy-gated excerpt and lineage | No generation side effect |
| `compare_evidence` | Current-policy project evidence table | Project authorization required |
| `compare_versions` | Deterministic section/locator alignment | At most 100 changes; textual change is not called substantive |
| `trace_citations` | Direct-citation traversal | Depth 3, 100 nodes, 200 edges |
| `trace_policy_links` | Typed research-policy traversal | Relationship types remain distinct |
| `export_project` | Governed immutable table export | Project authorization, revision, and idempotency required |

Every tool calls application services rather than providers. Inputs use closed
schemas, document text is returned as untrusted data, and no tool invokes
synthesis implicitly.

### Invocation audit

Each transport-valid call admitted to the application service writes an
append-only record containing actor, tool, outcome, duration, opaque target
identifiers, and SHA-256 digests of the input and result. Raw queries, excerpts,
project notes, and prompts are deliberately excluded. Schema-invalid requests
are rejected at the HTTP boundary, and a failed application audit write fails
the admitted tool call closed.

### Readiness and promotion

`eeg readiness` checks the canonical database, artifact store, OpenSearch,
database migration head, active index generation, and free artifact-disk space.
Its output names local limitations so a passing result cannot be mistaken for
an AWS or enterprise-security attestation.

`eeg promotion-manifest` seals the active corpus/index identity, migration and
API compatibility versions, source-registry checksum, and required pre-transfer
reviews. It moves no data and intentionally leaves the target profile
unassigned.

## Verification evidence

The automated suite covers all eight tools through service and HTTP contracts;
authorization denial; input, graph, and pagination bounds; unchanged, added,
removed, and changed version sections; truncation; PostgreSQL audit round trips;
Python client pagination, errors, jobs, projects, evidence, and exports; static R
tool parity; provider failure readiness; promotion checksums; and migration
packaging.

The repository quality gate remains `make check`.

## Deferred production gates

The implementation does not claim completion of AWS infrastructure, enterprise
federation, network and encryption approval, institutional retention, approved
recovery objectives, accessibility sign-off, real-corpus load targets, source
rights approval, stakeholder benchmark thresholds, or named operational-owner
acceptance. Those require organizational decisions and exercises with the
actual pilot corpus. The local controls and runbooks produce the evidence for
those reviews without fabricating their outcome.
