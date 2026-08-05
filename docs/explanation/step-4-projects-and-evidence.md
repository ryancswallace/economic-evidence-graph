# Step 4 projects and evidence

Step 4 implements the collaborative research layer on top of the governed corpus
and temporally safe search foundation. It is designed for an RA to hand a review
to an economist without losing screening history, source anchors, review state,
or the exact search and index dependencies that produced the work.

## Implemented scope

- Actor-attributed projects with owner, editor, reviewer, and viewer roles.
- Explicit local membership authorization without claiming enterprise identity.
- Optimistic project revisions on every mutation and deterministic idempotency
  keys for safe retries.
- Immutable saved-search attachments and append-only screening decisions, notes,
  and review events.
- Typed evidence records whose observed or extracted fields require exact,
  policy-deliverable evidence quotes and content hashes.
- User-authored analytical values that are explicitly distinguished from source
  observations and cannot carry implied source anchors.
- Declared evidence-table schemas, explicit missingness, and rows that reference
  exact evidence-field revisions.
- Separate direct-citation, explicit-mention, shared-source, and topical-suggestion
  policy links. Topical similarity is never labeled as influence.
- Governed JSON, CSV, and Markdown evidence-table exports with checksums,
  reproducibility manifests, source-evidence dependencies, saved-run IDs, review
  event IDs, and the project revision used.
- Immediate suppression and source-policy revalidation before export. Newly
  unavailable quotes are redacted and reported rather than copied from project
  history.
- A dependency-free project workbench at `/projects` plus versioned REST APIs.

## Trust invariants

### Collaboration

Every project write records an actor and an idempotency key. Owners manage
membership; owners and editors build research state; owners and reviewers make
review decisions; viewers are read-only. The local actor header is an attribution
and authorization mechanism for the pilot, not enterprise authentication.

Every mutation supplies the project revision it read. A stale revision fails with
a conflict instead of silently overwriting another collaborator's work. Screening
and review corrections are compensating events; prior decisions remain visible.

### Evidence

Observed transcription and deterministic extraction require one or more exact
anchors. Anchor creation resolves the current search projection, verifies the work
and document version, runs the immediate suppression and source-delivery gate, and
proves that the submitted quote occurs in the bounded delivered excerpt.

Approval is a separate review event. New values cannot start approved, rejected,
or superseded. Evidence tables reference immutable field IDs and require every
declared column to contain either a field reference or a controlled missingness
reason.

### Policy links

Direct citations and explicit mentions require an exact policy-document anchor.
Shared-source and topical links may be unanchored suggestions, but retain their
method, confidence, and review state. The API returns relationship types unchanged
and does not infer causal influence.

### Exports

Project exports are bounded, content-addressed artifacts. JSON retains structured
cells and anchors. CSV and Markdown retain stable work/version IDs, review state,
missingness, and evidence-unit IDs. CSV values that spreadsheet applications could
interpret as formulas are escaped. Each manifest records:

- project, project revision, table, creator, and creation time;
- baseline corpus snapshot and index generation;
- attached immutable search-run IDs;
- evidence-record and review-event IDs;
- evidence-unit IDs and content hashes;
- data URI, media type, checksum, byte length, and visible warnings.

## Local workflow

1. Run `eeg migrate` and `eeg serve` through the local composition.
2. Open `/`, run a search with **Save a reproducible search run**, and copy the run
   and evidence identifiers needed for review.
3. Open `/projects`, create a project, and attach the saved run.
4. Screen candidate works, transcribe anchored evidence, declare a comparison
   schema, and add study rows.
5. Add a reviewer, record approvals or uncertainty, and create typed policy links.
6. Export the reviewed table as JSON, CSV, or Markdown. Retain the generated
   manifest with the evidence pack.

## Deliberate boundaries

- Local actor attribution does not substitute for FRS enterprise identity,
  production row-level security, or institutional access review.
- Step 4 does not generate narrative synthesis or autonomous recommendations.
- A dedicated graph database is not used; bounded policy adjacency remains in
  PostgreSQL.
- Project history may preserve identifiers for suppressed evidence, but delivery
  and new exports never expose governed-away quotes.
- R/Python clients and typed agent tools are implemented by Step 5. AWS providers
  and institutional production controls remain deferred.

## Verification

The Step 4 acceptance suite exercises complete RA-to-economist handoff, all roles,
stale revisions, idempotent retries, exact quote validation, explicit missingness,
review transitions, policy tracing, suppression-safe exports, all three export
formats, API workflows, and memory/SQL provider parity. The repository's
authoritative `make check` remains the release gate.
