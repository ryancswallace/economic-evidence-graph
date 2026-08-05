"""Thread-safe in-memory providers for contract and API tests."""

from dataclasses import replace
from threading import RLock
from typing import Any, TypeVar, cast

from economic_evidence_graph.domain import (
    CorpusSearchRecord,
    EvidenceBundle,
    EvidenceTable,
    EvidenceTableRow,
    IdentifierValue,
    IndexGeneration,
    IngestedRecord,
    Job,
    JobState,
    PolicyDecisionRecord,
    PolicyLink,
    Project,
    ProjectBundle,
    ProjectEvidenceRecord,
    ProjectExport,
    ProjectMember,
    ProjectNote,
    ProjectSearchRun,
    ReviewEvent,
    RobotsObservation,
    ScreeningEvent,
    SearchRun,
    SourceMetadataRecord,
    SuppressionRecord,
    ToolInvocationRecord,
)
from economic_evidence_graph.exceptions import ConflictError, NotFoundError
from economic_evidence_graph.serialization import record_sha256

T = TypeVar("T")


class InMemoryRepository:
    """Combined catalog and job repository with production-equivalent invariants."""

    def __init__(self) -> None:
        self._bundles: dict[str, EvidenceBundle] = {}
        self._bundle_digests: dict[str, str] = {}
        self._jobs: dict[str, Job] = {}
        self._job_keys: dict[str, str] = {}
        self._ingested: dict[str, IngestedRecord] = {}
        self._ingested_digests: dict[str, str] = {}
        self._decisions: dict[str, PolicyDecisionRecord] = {}
        self._robots: dict[str, RobotsObservation] = {}
        self._suppressions: dict[str, SuppressionRecord] = {}
        self._index_generations: dict[str, IndexGeneration] = {}
        self._search_runs: dict[str, SearchRun] = {}
        self._projects: dict[str, Project] = {}
        self._project_members: dict[str, ProjectMember] = {}
        self._project_search_runs: dict[str, ProjectSearchRun] = {}
        self._screening_events: dict[str, ScreeningEvent] = {}
        self._project_notes: dict[str, ProjectNote] = {}
        self._project_evidence: dict[str, ProjectEvidenceRecord] = {}
        self._evidence_tables: dict[str, EvidenceTable] = {}
        self._evidence_table_rows: dict[str, EvidenceTableRow] = {}
        self._policy_links: dict[str, PolicyLink] = {}
        self._review_events: dict[str, ReviewEvent] = {}
        self._project_exports: dict[str, ProjectExport] = {}
        self._tool_invocations: dict[str, ToolInvocationRecord] = {}
        self._lock = RLock()

    def put_bundle(self, bundle: EvidenceBundle) -> None:
        """Persist an immutable bundle idempotently."""
        digest = record_sha256(bundle)
        with self._lock:
            prior_digest = self._bundle_digests.get(bundle.work.id)
            if prior_digest is not None and prior_digest != digest:
                raise ConflictError(
                    "stored work differs from the supplied immutable bundle",
                    context={"work_id": bundle.work.id},
                )
            self._bundles[bundle.work.id] = bundle
            self._bundle_digests[bundle.work.id] = digest

    def get_bundle(self, work_id: str) -> EvidenceBundle:
        """Return a stored evidence bundle."""
        with self._lock:
            try:
                return self._bundles[work_id]
            except KeyError as error:
                raise NotFoundError("work was not found", context={"work_id": work_id}) from error

    def add(self, item: Job) -> None:
        """Create a pending job with unique identifier and idempotency key."""
        if item.state is not JobState.PENDING:
            raise ConflictError("new jobs must be pending")
        with self._lock:
            if item.id in self._jobs or item.idempotency_key in self._job_keys:
                raise ConflictError("job identifier or idempotency key already exists")
            self._jobs[item.id] = item
            self._job_keys[item.idempotency_key] = item.id

    def save(self, item: Job) -> None:
        """Save a later valid job revision."""
        with self._lock:
            prior = self._jobs.get(item.id)
            if prior is None:
                raise NotFoundError("job was not found", context={"job_id": item.id})
            if prior.idempotency_key != item.idempotency_key:
                raise ConflictError("job idempotency keys are immutable")
            self._jobs[item.id] = item

    def get(self, job_id: str) -> Job:
        """Return a stored job."""
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as error:
                raise NotFoundError("job was not found", context={"job_id": job_id}) from error

    def find_by_idempotency_key(self, key: str) -> Job | None:
        """Find a job by its client operation key."""
        with self._lock:
            job_id = self._job_keys.get(key)
            return None if job_id is None else self._jobs[job_id]

    def check(self) -> None:
        """Confirm the in-memory provider is usable."""

    def current_migration_revision(self) -> str:
        """Represent the in-memory schema as current for contract tests."""
        from economic_evidence_graph.migrations import head_revision

        return head_revision()

    def put_ingested_record(self, item: IngestedRecord) -> None:
        """Persist one Step 2 result idempotently."""
        digest = record_sha256(
            (
                item.metadata,
                item.assets,
                item.documents,
                item.quarantines,
                item.acquisition_failures,
                item.identity_candidates,
                item.change,
            )
        )
        with self._lock:
            prior = self._ingested_digests.get(item.metadata.id)
            if prior is not None and prior != digest:
                raise ConflictError("stored ingestion result differs from immutable input")
            self._ingested[item.metadata.id] = item
            self._ingested_digests[item.metadata.id] = digest
            self._put_trace(item.decisions, item.robots)

    def put_governance_trace(
        self,
        decisions: tuple[PolicyDecisionRecord, ...],
        robots: tuple[RobotsObservation, ...],
    ) -> None:
        """Persist source-governance observations idempotently."""
        with self._lock:
            self._put_trace(decisions, robots)

    def latest_source_record(self, source_code: str, source_identifier: str) -> SourceMetadataRecord | None:
        """Return the newest matching source observation."""
        with self._lock:
            records = [
                result.metadata
                for result in self._ingested.values()
                if result.metadata.source_code == source_code and result.metadata.source_identifier == source_identifier
            ]
            return max(records, key=lambda item: (item.observed_at, item.id), default=None)

    def identity_matches(
        self,
        identifiers: tuple[IdentifierValue, ...],
        *,
        exclude_record_id: str,
    ) -> tuple[SourceMetadataRecord, ...]:
        """Return source records sharing an exact identifier."""
        needles = set(identifiers)
        with self._lock:
            matches = {
                result.metadata.id: result.metadata
                for result in self._ingested.values()
                if result.metadata.id != exclude_record_id and needles.intersection(result.metadata.identifiers)
            }
        return tuple(matches[key] for key in sorted(matches))

    def put_suppression(self, item: SuppressionRecord) -> None:
        """Persist an immutable suppression tombstone."""
        with self._lock:
            _put_memory_immutable(self._suppressions, item.id, item)

    def is_suppressed(self, scope_type: str, scope_value: str) -> bool:
        """Return whether a matching tombstone exists."""
        with self._lock:
            return any(
                item.scope_type == scope_type and item.scope_value == scope_value
                for item in self._suppressions.values()
            )

    def corpus_usage(self) -> tuple[int, int]:
        """Return persisted source-record count and raw acquired bytes."""
        with self._lock:
            return len(self._ingested), sum(
                asset.byte_length for result in self._ingested.values() for asset in result.assets
            )

    def list_corpus_search_records(self) -> tuple[CorpusSearchRecord, ...]:
        """Return joined Step 2 observations for deterministic search projection."""
        with self._lock:
            records: list[CorpusSearchRecord] = []
            for key in sorted(self._ingested):
                item = self._ingested[key]
                source_key = f"{item.metadata.source_code}:{item.metadata.source_identifier}"
                blocked = any(
                    suppression.scope_type == "source_record" and suppression.scope_value == source_key
                    for suppression in self._suppressions.values()
                )
                blocked = blocked or any(
                    suppression.scope_type == "url"
                    and suppression.scope_value
                    in {
                        item.metadata.metadata_url,
                        *(asset.requested_url for asset in item.assets),
                        *(asset.resolved_url for asset in item.assets),
                    }
                    for suppression in self._suppressions.values()
                )
                blocked = blocked or any(
                    suppression.scope_type == "asset"
                    and any(suppression.scope_value == asset.id for asset in item.assets)
                    for suppression in self._suppressions.values()
                )
                records.append(CorpusSearchRecord(item.metadata, item.assets, item.documents, blocked))
            return tuple(records)

    def list_suppression_scopes(self) -> tuple[tuple[str, str], ...]:
        """Return current suppression scopes in deterministic order."""
        with self._lock:
            return tuple(sorted((item.scope_type, item.scope_value) for item in self._suppressions.values()))

    def put_index_generation(self, item: IndexGeneration) -> None:
        """Persist one immutable index generation."""
        with self._lock:
            _put_memory_immutable(self._index_generations, item.id, item)

    def latest_index_generation(self) -> IndexGeneration | None:
        """Return the most recently activated generation."""
        with self._lock:
            return max(
                self._index_generations.values(),
                key=lambda item: (item.activated_at, item.id),
                default=None,
            )

    def get_index_generation(self, generation_id: str) -> IndexGeneration | None:
        """Return one index generation by identifier."""
        with self._lock:
            return self._index_generations.get(generation_id)

    def put_search_run(self, item: SearchRun) -> None:
        """Persist one immutable search run."""
        with self._lock:
            _put_memory_immutable(self._search_runs, item.id, item)

    def get_search_run(self, run_id: str) -> SearchRun | None:
        """Return one search run by identifier."""
        with self._lock:
            return self._search_runs.get(run_id)

    def create_project(self, item: Project, owner: ProjectMember) -> Project:
        """Create a project and owner atomically and idempotently."""
        if owner.project_id != item.id or owner.actor_id != item.created_by:
            raise ConflictError("project owner attribution does not match its creator")
        with self._lock:
            prior = self._projects.get(item.id)
            if prior is not None:
                if not _same_project_creation(prior, item):
                    raise ConflictError("project idempotency key conflicts with stored state")
                return prior
            self._projects[item.id] = item
            self._project_members[owner.id] = owner
            return item

    def get_project(self, project_id: str) -> Project:
        """Return one project."""
        with self._lock:
            try:
                return self._projects[project_id]
            except KeyError as error:
                raise NotFoundError("project was not found", context={"project_id": project_id}) from error

    def list_projects(self, actor_id: str) -> tuple[Project, ...]:
        """Return projects visible through explicit membership."""
        with self._lock:
            project_ids = {item.project_id for item in self._project_members.values() if item.actor_id == actor_id}
            return tuple(
                sorted(
                    (self._projects[item] for item in project_ids),
                    key=lambda project: (project.created_at, project.id),
                )
            )

    def get_project_bundle(self, project_id: str) -> ProjectBundle:
        """Return complete project state and history."""
        with self._lock:
            try:
                project = self._projects[project_id]
            except KeyError as error:
                raise NotFoundError("project was not found", context={"project_id": project_id}) from error
            return ProjectBundle(
                project=project,
                members=_memory_project_items(self._project_members, project_id),
                search_runs=_memory_project_items(self._project_search_runs, project_id),
                screening_events=_memory_project_items(self._screening_events, project_id),
                notes=_memory_project_items(self._project_notes, project_id),
                evidence_records=_memory_project_items(self._project_evidence, project_id),
                evidence_tables=_memory_project_items(self._evidence_tables, project_id),
                evidence_table_rows=_memory_project_items(self._evidence_table_rows, project_id),
                policy_links=_memory_project_items(self._policy_links, project_id),
                review_events=_memory_project_items(self._review_events, project_id),
                exports=_memory_project_items(self._project_exports, project_id),
            )

    def put_project_member(self, item: ProjectMember, *, expected_revision: int) -> Project:
        """Add one project member."""
        return self._put_project_item(self._project_members, item, expected_revision)

    def put_project_search_run(self, item: ProjectSearchRun, *, expected_revision: int) -> Project:
        """Attach one saved search run."""
        return self._put_project_item(self._project_search_runs, item, expected_revision)

    def put_screening_event(self, item: ScreeningEvent, *, expected_revision: int) -> Project:
        """Append one screening event."""
        return self._put_project_item(self._screening_events, item, expected_revision)

    def put_project_note(self, item: ProjectNote, *, expected_revision: int) -> Project:
        """Append one project note."""
        return self._put_project_item(self._project_notes, item, expected_revision)

    def put_project_evidence_record(self, item: ProjectEvidenceRecord, *, expected_revision: int) -> Project:
        """Persist one project evidence record."""
        return self._put_project_item(self._project_evidence, item, expected_revision)

    def put_evidence_table(self, item: EvidenceTable, *, expected_revision: int) -> Project:
        """Persist one evidence table schema."""
        return self._put_project_item(self._evidence_tables, item, expected_revision)

    def put_evidence_table_row(self, item: EvidenceTableRow, *, expected_revision: int) -> Project:
        """Persist one evidence comparison row."""
        return self._put_project_item(self._evidence_table_rows, item, expected_revision)

    def put_policy_link(self, item: PolicyLink, *, expected_revision: int) -> Project:
        """Persist one typed research-policy link."""
        return self._put_project_item(self._policy_links, item, expected_revision)

    def put_review_event(self, item: ReviewEvent, *, expected_revision: int) -> Project:
        """Append one review event."""
        return self._put_project_item(self._review_events, item, expected_revision)

    def get_review_event(self, event_id: str) -> ReviewEvent | None:
        """Return one review event."""
        with self._lock:
            return self._review_events.get(event_id)

    def put_project_export(self, item: ProjectExport, *, expected_revision: int) -> Project:
        """Persist one project export."""
        return self._put_project_item(self._project_exports, item, expected_revision)

    def get_project_export(self, export_id: str) -> ProjectExport:
        """Return one project export."""
        with self._lock:
            try:
                return self._project_exports[export_id]
            except KeyError as error:
                raise NotFoundError("project export was not found", context={"export_id": export_id}) from error

    def put_tool_invocation(self, item: ToolInvocationRecord) -> None:
        """Append one privacy-safe tool invocation idempotently."""
        with self._lock:
            _put_memory_immutable(self._tool_invocations, item.id, item)

    def list_tool_invocations(self, *, limit: int = 100) -> tuple[ToolInvocationRecord, ...]:
        """Return newest tool invocations under a hard operational bound."""
        with self._lock:
            ordered = sorted(
                self._tool_invocations.values(),
                key=lambda item: (item.occurred_at, item.id),
                reverse=True,
            )
            return tuple(ordered[: max(0, min(limit, 1_000))])

    @property
    def ingested_records(self) -> tuple[IngestedRecord, ...]:
        """Return stored Step 2 records for contract tests and local inspection."""
        with self._lock:
            return tuple(self._ingested[key] for key in sorted(self._ingested))

    def _put_trace(
        self,
        decisions: tuple[PolicyDecisionRecord, ...],
        robots: tuple[RobotsObservation, ...],
    ) -> None:
        """Store trace items while enforcing immutable identifiers."""
        for item in decisions:
            _put_memory_immutable(self._decisions, item.id, item)
        for item in robots:
            _put_memory_immutable(self._robots, item.id, item)

    def _put_project_item(self, target: dict[str, T], item: T, expected_revision: int) -> Project:
        """Idempotently append one item and atomically bump its project revision."""
        project_item = cast(Any, item)
        project_id = str(project_item.project_id)
        identifier = str(project_item.id)
        with self._lock:
            project = self._projects.get(project_id)
            if project is None:
                raise NotFoundError("project was not found", context={"project_id": project_id})
            prior = target.get(identifier)
            if prior is not None:
                if record_sha256(prior) != record_sha256(item):
                    raise ConflictError("project operation idempotency key conflicts with stored state")
                return project
            if project.revision != expected_revision:
                raise ConflictError(
                    "project was modified concurrently",
                    context={"expected_revision": str(expected_revision), "actual_revision": str(project.revision)},
                )
            target[identifier] = item
            revised = replace(project, revision=project.revision + 1)
            self._projects[project_id] = revised
            return revised

    def close(self) -> None:
        """Release no resources for the in-memory provider."""


def _put_memory_immutable(target: dict[str, T], identifier: str, item: T) -> None:
    """Insert or verify an immutable in-memory record."""
    prior = target.get(identifier)
    if prior is not None and record_sha256(prior) != record_sha256(item):
        raise ConflictError("governance observation identifier conflicts with stored state")
    target[identifier] = item


def _memory_project_items(target: dict[str, T], project_id: str) -> tuple[T, ...]:
    """Return same-project records in deterministic timestamp/identifier order."""
    items = (item for item in target.values() if getattr(item, "project_id", None) == project_id)
    return tuple(
        sorted(
            items,
            key=lambda item: (
                str(getattr(item, "created_at", "")),
                str(cast(Any, item).id),
            ),
        )
    )


def _same_project_creation(left: Project, right: Project) -> bool:
    """Compare immutable create inputs while ignoring server time and later revision."""
    return (
        left.id,
        left.name,
        left.description,
        left.created_by,
        left.baseline_corpus_snapshot_id,
        left.baseline_index_generation_id,
        left.idempotency_key,
    ) == (
        right.id,
        right.name,
        right.description,
        right.created_by,
        right.baseline_corpus_snapshot_id,
        right.baseline_index_generation_id,
        right.idempotency_key,
    )
