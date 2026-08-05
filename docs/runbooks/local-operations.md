# Local operations and pilot acceptance

This runbook produces evidence for a local pilot handoff. A successful local
exercise is not AWS production approval.

## Start and preflight

```bash
make local-up
uv run eeg migrate
uv run eeg rebuild-search
uv run eeg readiness
```

Do not proceed if readiness reports a failed provider, migration mismatch,
missing active index generation, or insufficient disk headroom. Record the JSON
output with the pilot date, corpus snapshot, index generation, application
commit, host model, memory, and available disk.

## Routine operating checks

Before a review session:

1. Confirm `/health/live`, `/health/ready`, and `eeg readiness`.
2. Confirm connector freshness and inspect quarantine and acquisition failures.
3. Run one lexical, semantic, and strict historical reference query.
4. Resolve one result to an exact evidence locator and source manifestation.
5. Inspect recent tool audit outcomes with `eeg tool-audit --limit 100`.
6. Preserve project export data and manifest together.

Audit output is content-minimized, but actor and project identifiers are still
internal operational data and must follow the approved access and retention
policy.

## Backup and recovery exercise

Back up the canonical PostgreSQL database and content-addressed artifact root.
OpenSearch is a derivative and should be rebuilt from canonical records rather
than treated as the only recoverable copy. Store backups outside the live
Compose volumes with restricted local permissions.

A recovery exercise must restore into a new, explicitly named scratch database
and artifact directory. It must not overwrite the active pilot corpus. After
restore:

1. Apply packaged migrations to the scratch database.
2. Verify row counts and sampled stable identifiers.
3. Verify sampled artifact SHA-256 checksums.
4. Rebuild a new OpenSearch generation and activate its aliases.
5. Replay sampled current and historical searches.
6. Resolve sampled evidence anchors and compare a project export manifest.
7. Record elapsed recovery time and data loss, then compare them with approved
   recovery objectives once those objectives exist.

Do not call the service production-ready until an owner has approved backup
location, frequency, encryption, retention, recovery point, and recovery time.

## Local load and capacity exercise

The first acceptance slice is 1,000 document versions and 50,000 evidence
units. Record ingestion throughput, index-build duration, API p50/p95/p99,
resident memory, swap, disk growth, thermal throttling, zero-result rate, and
provider errors. Exercise current search, strict historical search, evidence
retrieval, project handoff, all eight tools, and export.

Stop expansion if the host swaps persistently, disk headroom falls below the
configured threshold, OpenSearch becomes red, provenance validation fails, or
later information leaks into a strict historical result. The 10,000-version and
500,000-evidence-unit target is conditional on the measured first slice.

## Accessibility and user acceptance

Automated HTML and API tests do not replace manual review. On each supported
browser, test keyboard-only operation, visible focus, zoom, contrast, labels,
linear evidence-table reading, reader highlights, status announcements, and
error recovery. Complete the signature workflows with economists and RAs and
retain task time, failure category, provenance errors, and qualitative notes.

## Promotion evidence

Create a provider-neutral pre-transfer manifest only after local checks pass:

```bash
uv run eeg promotion-manifest --output dist/eeg-promotion-manifest.json
```

Review the source-registry checksum, active generation, migration revision,
contract identities, and required pre-transfer reviews. The command copies no
data, selects no AWS target, and grants no source-transfer permission.

## Incident and shutdown

For suspected source-policy bypass, cross-project access, prompt injection, or
corrupt evidence, preserve request and opaque object IDs, stop the affected
workflow, create a suppression record when authorized, and retain logs without
copying sensitive query or evidence content into tickets.

Stop normally with `make local-down`. This preserves the named volumes. Volume
deletion is intentionally absent from repository Make targets.
