# Step 3 search and historical pilot

Step 3 turns the immutable Step 2 corpus into an explainable, policy-aware
literature-search system. It builds versioned OpenSearch indexes, applies strict
historical eligibility before candidate generation, fuses lexical and
deterministic semantic retrieval, collapses exact work versions, and returns a
bounded evidence excerpt with exact lineage.

## Implemented boundary

The implementation includes:

- a provisional search read model over every unsuppressed source observation;
- exact DOI and RePEc work grouping without promoting unreviewed fuzzy merges;
- immutable work and evidence index generations with atomic current aliases;
- lexical work/evidence, deterministic semantic work/evidence, and structured
  filter channels;
- weighted reciprocal-rank fusion with every rank and contribution in the
  response;
- strict as-of filtering inside every provider query, before scoring;
- source, publication-date, temporal-safety, and availability filters;
- version collapse after fusion, with alternative counts and selection reasons;
- policy-limited 500-character excerpts with acquisition, parser, locator, and
  checksum lineage;
- generation-bound cursor pagination and immutable saved search-run responses;
- a typed HTTP API, terminal client, and dependency-free local research
  workbench.

The search projection is deliberately not the canonical identity graph. A DOI
or RePEc identifier is sufficient to group source versions for retrieval; all
other source observations retain separate work identities. Review-driven
merge/split decisions remain a later canonical workflow.

## Historical eligibility

Eligibility uses conservative, inspectable evidence:

1. an exact official Board/FOMC release timestamp;
2. the first system observation for every other metadata record because source
   date assertions are not themselves observations under strict policy;
3. the first exact content retrieval for document text, even when the
   associated metadata has an earlier official release timestamp.

Source-asserted publication and revision dates remain visible as uncertainty
evidence, but do not backdate ordinary metadata or later-retrieved bytes. This
means a newly built corpus can initially return metadata-only historical results
or no results at a very early cutoff. Useful historical full-text coverage must
come from contemporaneous observations or a reviewed archive, not an assumption
that today's bytes are unchanged.

Strict requests permit only T0 source facts and T1 deterministic artifacts. The
cutoff, eligible-from timestamp, basis, source evidence, uncertainty, and safety
class are returned for every result. Tests create otherwise highly relevant
post-cutoff records and prove that they do not enter lexical, vector, or
structured candidate sets.

## Deterministic semantic baseline

The initial semantic channel uses a versioned, economics-aware feature-hashing
provider. It expands a small checked-in economics vocabulary, hashes tokens and
bigrams into a normalized vector, and has no learned state. This makes it a T1
transformation that is reproducible on the reference Mac and safe for the
strict historical contract.

This is an honest pilot baseline, not a claim that feature hashing matches a
modern learned embedding model. The provider boundary, model configuration ID,
vector dimension, and immutable generation behavior allow a reviewed
open-weight model to replace it later. A learned model will be T2 and must pass
the historical-certification benchmark before strict as-of queries may use it.

## Retrieval and delivery contract

Each search result identifies its work, source observation, document version,
source URL, source-policy version, index generation, and corpus snapshot. An
evidence-bearing result additionally includes the normalized document, acquired
asset, structural locator, source and text hashes, retrieval time, parser and
schema versions, and quality warnings.

Unknown rights permit governed internal indexing but never full-text delivery.
The source policy independently evaluates attributed excerpt display. If that
action is denied, the result remains discoverable as metadata and explicitly
states that matched evidence was withheld.

Saved-run replay returns the exact stored response and generation ID. It never
silently executes the request against a newer current alias. This initial slice
does not yet preserve old OpenSearch generations forever or re-run an old query
against an old alias; retention policy and dependency-availability records are
production-readiness work.

## Operator and client surfaces

Build and activate a generation after ingestion:

```bash
uv run eeg rebuild-search
```

Run a current or historical search:

```bash
uv run eeg search "monetary policy inflation"
uv run eeg search --as-of 2020-03-15T00:00:00Z \
  --source board_fomc "forward guidance expectations"
```

Start the API and open `http://127.0.0.1:8000/` for the workbench. The same
operation is available at `POST /v1/search`; evidence excerpts and saved runs
use `/v1/evidence/{id}` and `/v1/search/runs/{id}`.

## Verification

The provider contract is tested against both the in-memory implementation and a
stateful OpenSearch HTTP simulation. SQL tests rebuild from joined Step 2
records and round-trip generations and saved runs. API tests cover the
workbench, search, evidence reader, replay, readiness, and structured errors.
The complete repository suite includes the cutoff leakage regression and must
maintain at least 95 percent branch-aware coverage.

The live acceptance gate additionally migrates PostgreSQL, rebuilds the real
single-node OpenSearch index from the bounded local corpus, runs a representative
hybrid query, and checks both aliases and result provenance.

## Live local acceptance

The August 4, 2026 acceptance run projected 59 immutable source observations
into 50 distinct content versions and 2,190 evidence units. Nine unchanged
observations were consolidated deterministically; a count mismatch stopped the
first attempted activation before any alias changed, which validated the
generation gate. The corrected work and evidence indexes both passed exact
count checks and were switched together to generation
`idxgen_906dc8fb50525c4c943997fdd71ff97a`.

A current-corpus query for `monetary policy inflation` used all four default
work/evidence lexical and semantic channels and returned a fully attributed
excerpt with content and text hashes. Contributions were limited to the best
passage per version per channel so long documents could not accumulate ranking
weight merely by having more units.

The same query with a strict `2025-12-31T23:59:59Z` cutoff returned zero results.
Every pilot byte and ordinary metadata observation was first collected in 2026;
the system therefore refused to backdate them from asserted 2025 publication
labels. The empty result is the intended no-leakage behavior. Both current
aliases point to the corrected generation, and the live readiness endpoint and
research workbench return HTTP 200.
