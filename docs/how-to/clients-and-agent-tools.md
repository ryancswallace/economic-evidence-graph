# Use clients and agent tools

Start the local API and confirm readiness before using a client:

```bash
uv run eeg serve
uv run eeg readiness
```

Local actor identifiers provide attribution and project authorization for the
pilot. They are not enterprise authentication.

## Python

The client ships in the main package:

```python
from economic_evidence_graph import EegClient
from economic_evidence_graph.api.schemas import SearchCreateRequest

with EegClient(actor_id="ra-example") as client:
    request = SearchCreateRequest(
        query="forward guidance inflation expectations",
        limit=10,
        save_run=True,
    )
    for result in client.iter_search(request, max_pages=3):
        print(result.work_id, result.document_version_id, result.title)
```

Use `retrieve_evidence()` for exact excerpts, `create_project()` and
`get_project()` for handoffs, `compare_evidence()` for governed tables, and
`export_project()` plus `download_project_export()` for immutable output. An
`EegClientError` exposes `status`, `code`, `request_id`, `retryable`, and safe
context fields.

## R

Install the repository client with an approved local R package workflow, then:

```r
library(eegR)

client <- eeg_client(actor_id = "ra-example")
results <- eeg_search_all(
  client,
  "forward guidance inflation expectations",
  limit = 10L,
  max_pages = 3L
)
```

The package source is under `clients/r/eegR`. It uses `httr2`, preserves opaque
cursors and stable identifiers, and raises an `eeg_api_error` with the shared
problem fields.

## Typed agent tools

Discover the exact current schemas at `GET /v1/agent-tools`. Each listed tool has
one POST endpoint under `/v1/agent-tools/{name}` and rejects undocumented fields.
For example:

```bash
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --header 'X-EEG-Actor: ra-example' \
  --data '{"query":"monetary policy transmission","limit":5}' \
  http://127.0.0.1:8000/v1/agent-tools/search_literature
```

An agent adapter must register only the returned schemas, treat retrieved text
as untrusted data, preserve citations and warnings, and surface truncation. It
must not retry non-retryable problems, expand bounds, infer causal influence
from typed adjacency, or bypass project authorization.

Inspect content-minimized audit records as an operator:

```bash
uv run eeg tool-audit --limit 100
```

The audit contains digests and opaque identifiers, not raw queries or evidence
text.
