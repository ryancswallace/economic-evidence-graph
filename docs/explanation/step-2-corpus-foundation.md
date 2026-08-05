# Step 2 corpus foundation

Step 2 implements a governed, local-first path from five approved public source
families to immutable metadata observations, content-addressed raw assets,
normalized structural units, quality reports, quarantine records, change
events, durable acquisition failures, and conservative identity candidates.

## Approved pilot boundary

- Boston Fed research and policy publications: 2010-present.
- Board FEDS and IFDP papers: 2010-present.
- FOMC statements, minutes, and projections: 2000-present.
- RePEc, Crossref, and CORE augment the Federal Reserve seed corpus.
- The initial ceiling is 1,000 records or 10 GiB on the 48 GiB Apple Silicon
  reference host.

Unknown rights do not prevent internal retrieval when the source policy and
robots rules permit it. Such content is explicitly `rights_unknown` and the
same policy engine denies full-text display, export, and redistribution.
Metadata and attributed excerpts remain separately controlled actions.

## Ingestion path

1. A connector discovers a bounded cursor page.
2. Every HTTP hop is evaluated against the versioned source registry. Redirects
   are re-evaluated; private, loopback, link-local, and local-name targets are
   rejected.
3. Robots responses and action decisions become immutable audit observations.
   RePEc uses the project's public `RePEc-ReDIF` rsync module, avoiding website
   scraping.
4. A denied, unavailable, or non-success content link becomes a durable
   source-record-scoped acquisition failure. It does not discard valid metadata
   or prevent other representations from being acquired.
5. Raw metadata and content use SHA-256 content addresses. Exact replays are
   idempotent; later observations produce explicit `new`, `unchanged`, or
   `revised` events.
6. Format detection routes PDF, HTML, XML, JSON, and plain text through bounded
   deterministic parsers. Active, encrypted, malformed, empty, oversized, and
   image-only files enter quarantine with stable reasons. Image-only PDFs are
   marked `ocr_required`; an open-source OCR provider can be injected without
   changing the normalized contract.
7. Exact DOI or RePEc identifier overlap produces a reviewable identity
   candidate. Step 2 never silently merges works.

The SQL migration preserves each layer in explicit tables. Normalized documents
and units retain parser version, structural locator, page bounds when available,
quality signals, and the exact source and text artifact hashes.

## Operator surface

`eeg connector-smoke --source SOURCE` runs a ten-record live acceptance slice.
`eeg ingest-source --source SOURCE --limit 25` runs a normal bounded page. Both
commands migrate first, read the CORE key from the ignored `.env`, print only
non-secret counts, failure categories, and the next cursor, and close network
and database pools. In container mode, pass the ignored file with
`docker compose run --rm --env-from-file .env api ...`; host mode reads it
directly.

## Live connector acceptance

The authorized August 4, 2026 acceptance run used ten candidate records per
source. All five connectors committed all ten metadata records with zero
record-level failures.

| Source | Acquired assets | Normalized documents | Quarantined assets | Acquisition failures |
| --- | ---: | ---: | ---: | ---: |
| Crossref | 2 | 0 | 2 | 3 |
| CORE | 0 | 0 | 0 | 5 |
| RePEc | 10 | 10 | 0 | 9 |
| Boston Fed | 10 | 10 | 0 | 14 |
| Board/FOMC | 20 | 20 | 0 | 0 |

Acquisition failures in this slice were either HTTP 403 responses or governed
policy/robots denials. They are expected evidence about representation
availability, not silently dropped errors. All 44 acquired-asset observations
were conservatively classified `rights_unknown`; no live source assertion was
promoted to an open-rights status. Live results are endpoint observations, not
a guarantee that future source responses will have the same shape.

Step 3 can build retrieval indexes over this model without redefining source
rights, canonical text units, quarantine semantics, or identifier evidence.
