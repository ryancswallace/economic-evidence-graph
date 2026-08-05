"""Collaborative project, evidence review, policy-link, and export services."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import TypeVar, cast

from economic_evidence_graph.domain import (
    EvidenceLineageMethod,
    EvidenceState,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    PolicyLink,
    PolicyLinkType,
    Project,
    ProjectBundle,
    ProjectEvidenceAnchor,
    ProjectEvidenceField,
    ProjectEvidenceRecord,
    ProjectExport,
    ProjectExportFormat,
    ProjectMember,
    ProjectNote,
    ProjectRole,
    ProjectSearchRun,
    ReviewEvent,
    ReviewTargetType,
    ScreeningEvent,
    ScreeningOutcome,
    review_state,
    stable_id,
)
from economic_evidence_graph.domain.models import ScalarValue
from economic_evidence_graph.exceptions import (
    AuthorizationError,
    ConflictError,
    EconomicEvidenceGraphError,
    NotFoundError,
    PolicyDeniedError,
)
from economic_evidence_graph.ports import (
    ArtifactRef,
    ArtifactStore,
    Clock,
    ProjectRepository,
    SearchIndex,
    SearchRepository,
)
from economic_evidence_graph.serialization import JsonValue, canonical_json_bytes, to_json_value

PROJECT_EVIDENCE_SCHEMA_VERSION = "eeg.project-evidence.v1"
PROJECT_EXPORT_SCHEMA_VERSION = "eeg.project-export.v1"
TItem = TypeVar("TItem")


@dataclass(frozen=True, slots=True)
class EvidenceAnchorInput:
    """Untrusted client anchor request resolved against current governed evidence."""

    evidence_unit_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class EvidenceFieldInput:
    """Client field proposal before identifiers and trusted anchors are assigned."""

    field_name: str
    display_value: str
    normalized_value: ScalarValue
    units: str | None
    applicability: str
    missingness_reason: str | None
    state: EvidenceState
    lineage_method: EvidenceLineageMethod
    confidence: float
    anchors: tuple[EvidenceAnchorInput, ...] = ()


@dataclass(frozen=True, slots=True)
class TableCellInput:
    """Client comparison-cell selection before domain validation."""

    column_key: str
    evidence_field_id: str | None = None
    missingness_reason: str | None = None


class ProjectService:
    """Authorize and orchestrate the complete Step 4 collaborative workflow."""

    def __init__(
        self,
        *,
        repository: ProjectRepository,
        search_repository: SearchRepository,
        search_index: SearchIndex,
        retrieval: object,
        artifacts: ArtifactStore,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._search_repository = search_repository
        self._search_index = search_index
        self._retrieval = retrieval
        self._artifacts = artifacts
        self._clock = clock

    def create(
        self,
        *,
        name: str,
        description: str,
        actor_id: str,
        idempotency_key: str,
    ) -> Project:
        """Create a project pinned to the active corpus/index baseline, if present."""
        _require_actor(actor_id)
        project_id = stable_id("prj", actor_id, idempotency_key)
        try:
            prior = self._repository.get_project(project_id)
        except NotFoundError:
            prior = None
        if prior is not None:
            if (
                prior.name == name
                and prior.description == description
                and prior.created_by == actor_id
                and prior.idempotency_key == idempotency_key
            ):
                return prior
            raise ConflictError("project idempotency key was reused for different create input")
        generation = self._search_repository.latest_index_generation()
        now = self._clock.now()
        project = Project(
            id=project_id,
            name=name,
            description=description,
            created_by=actor_id,
            created_at=now,
            baseline_corpus_snapshot_id=None if generation is None else generation.corpus_snapshot_id,
            baseline_index_generation_id=None if generation is None else generation.id,
            idempotency_key=idempotency_key,
        )
        owner = ProjectMember(
            id=stable_id("pmem", project_id, actor_id),
            project_id=project_id,
            actor_id=actor_id,
            role=ProjectRole.OWNER,
            added_by=actor_id,
            added_at=now,
            idempotency_key=idempotency_key,
        )
        return self._repository.create_project(project, owner)

    def list(self, *, actor_id: str) -> tuple[Project, ...]:
        """List projects visible to an actor."""
        _require_actor(actor_id)
        return self._repository.list_projects(actor_id)

    def get(self, project_id: str, *, actor_id: str) -> ProjectBundle:
        """Return a complete authorized project handoff."""
        bundle = self._repository.get_project_bundle(project_id)
        _role(bundle, actor_id)
        return bundle

    def add_member(
        self,
        project_id: str,
        *,
        actor_id: str,
        member_actor_id: str,
        role: ProjectRole,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Add a collaborator; only an owner may change membership."""
        bundle = self.get(project_id, actor_id=actor_id)
        if _role(bundle, actor_id) is not ProjectRole.OWNER:
            raise AuthorizationError("only a project owner may add collaborators")
        existing = next((item for item in bundle.members if item.actor_id == member_actor_id), None)
        if existing is not None:
            if existing.role is role:
                return bundle.project
            raise ConflictError("project member roles are immutable in the Step 4 pilot")
        item = ProjectMember(
            id=stable_id("pmem", project_id, member_actor_id),
            project_id=project_id,
            actor_id=member_actor_id,
            role=role,
            added_by=actor_id,
            added_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_project_member(item, expected_revision=expected_revision)

    def attach_search_run(
        self,
        project_id: str,
        *,
        actor_id: str,
        search_run_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Attach one exact saved-search manifest for reproducible handoff."""
        self._require_editor(project_id, actor_id)
        run = self._search_repository.get_search_run(search_run_id)
        if run is None:
            raise NotFoundError("saved search run was not found", context={"run_id": search_run_id})
        bundle = self.get(project_id, actor_id=actor_id)
        if any(item.search_run_id == search_run_id for item in bundle.search_runs):
            return bundle.project
        item = ProjectSearchRun(
            id=stable_id("psr", project_id, idempotency_key),
            project_id=project_id,
            search_run_id=run.id,
            saved_by=actor_id,
            saved_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_project_search_run(item, expected_revision=expected_revision)

    def screen(
        self,
        project_id: str,
        *,
        actor_id: str,
        work_id: str,
        document_version_id: str | None,
        outcome: ScreeningOutcome,
        reason_code: str | None,
        note: str | None,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Append a controlled screening decision, including compensating decisions."""
        self._require_editor(project_id, actor_id)
        item = ScreeningEvent(
            id=stable_id("scr", project_id, idempotency_key),
            project_id=project_id,
            work_id=work_id,
            document_version_id=document_version_id,
            outcome=outcome,
            reason_code=reason_code,
            note=note,
            decided_by=actor_id,
            decided_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_screening_event(item, expected_revision=expected_revision)

    def add_note(
        self,
        project_id: str,
        *,
        actor_id: str,
        target_type: str,
        target_id: str,
        body: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Append an attributed note to a stable object."""
        self._require_editor(project_id, actor_id)
        item = ProjectNote(
            id=stable_id("pnote", project_id, idempotency_key),
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            body=body,
            created_by=actor_id,
            created_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_project_note(item, expected_revision=expected_revision)

    def add_evidence_record(
        self,
        project_id: str,
        *,
        actor_id: str,
        work_id: str,
        document_version_id: str,
        record_type: str,
        fields: tuple[EvidenceFieldInput, ...],
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Create a typed record only after every claimed source anchor validates."""
        self._require_editor(project_id, actor_id)
        record_id = stable_id("pevd", project_id, idempotency_key)
        now = self._clock.now()
        domain_fields: list[ProjectEvidenceField] = []
        for ordinal, field in enumerate(fields):
            _validate_initial_field_state(field)
            anchors = tuple(
                self._resolve_anchor(item, expected_work_id=work_id, expected_version_id=document_version_id)
                for item in field.anchors
            )
            domain_fields.append(
                ProjectEvidenceField(
                    id=stable_id("pevf", record_id, str(ordinal), field.field_name),
                    field_name=field.field_name,
                    display_value=field.display_value,
                    normalized_value=field.normalized_value,
                    units=field.units,
                    applicability=field.applicability,
                    missingness_reason=field.missingness_reason,
                    state=field.state,
                    lineage_method=field.lineage_method,
                    confidence=field.confidence,
                    anchors=anchors,
                    created_by=actor_id,
                    created_at=now,
                )
            )
        item = ProjectEvidenceRecord(
            id=record_id,
            project_id=project_id,
            work_id=work_id,
            document_version_id=document_version_id,
            record_type=record_type,
            schema_version=PROJECT_EVIDENCE_SCHEMA_VERSION,
            fields=tuple(domain_fields),
            created_by=actor_id,
            created_at=now,
            idempotency_key=idempotency_key,
        )
        return self._repository.put_project_evidence_record(item, expected_revision=expected_revision)

    def create_evidence_table(
        self,
        project_id: str,
        *,
        actor_id: str,
        name: str,
        description: str,
        columns: tuple[EvidenceTableColumn, ...],
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Create a declared comparison schema before adding analytical rows."""
        self._require_editor(project_id, actor_id)
        item = EvidenceTable(
            id=stable_id("etbl", project_id, idempotency_key),
            project_id=project_id,
            name=name,
            description=description,
            columns=columns,
            created_by=actor_id,
            created_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_evidence_table(item, expected_revision=expected_revision)

    def add_evidence_table_row(
        self,
        project_id: str,
        table_id: str,
        *,
        actor_id: str,
        evidence_record_id: str,
        label: str,
        cells: tuple[TableCellInput, ...],
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Add a complete comparison row whose cells resolve to same-project fields."""
        bundle = self._require_editor(project_id, actor_id)
        table = _find(bundle.evidence_tables, table_id, "evidence table")
        record = _find(bundle.evidence_records, evidence_record_id, "project evidence record")
        if any(
            item.table_id == table_id and item.evidence_record_id == evidence_record_id
            for item in bundle.evidence_table_rows
        ):
            raise ConflictError("this evidence record already has a row in the selected table")
        requested_keys = {item.column_key for item in cells}
        column_keys = {item.key for item in table.columns}
        if requested_keys != column_keys:
            raise EconomicEvidenceGraphError("evidence table rows must explicitly populate every declared column")
        fields = {item.id: item for item in record.fields}
        domain_cells: list[EvidenceTableCell] = []
        for item in cells:
            field = None if item.evidence_field_id is None else fields.get(item.evidence_field_id)
            if item.evidence_field_id is not None and field is None:
                raise EconomicEvidenceGraphError("evidence table cell references a field outside its record")
            if field is not None and field.field_name != item.column_key:
                raise EconomicEvidenceGraphError("evidence table cell field does not match its declared column")
            domain_cells.append(EvidenceTableCell(item.column_key, item.evidence_field_id, item.missingness_reason))
        row = EvidenceTableRow(
            id=stable_id("etrow", project_id, idempotency_key),
            project_id=project_id,
            table_id=table_id,
            evidence_record_id=evidence_record_id,
            label=label,
            cells=tuple(domain_cells),
            created_by=actor_id,
            created_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_evidence_table_row(row, expected_revision=expected_revision)

    def add_policy_link(
        self,
        project_id: str,
        *,
        actor_id: str,
        research_work_id: str,
        policy_work_id: str,
        relationship_type: PolicyLinkType,
        anchor: EvidenceAnchorInput | None,
        method: str,
        confidence: float,
        state: EvidenceState,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Create a typed policy link without relabeling topical similarity as influence."""
        self._require_editor(project_id, actor_id)
        if state not in {EvidenceState.OBSERVED, EvidenceState.SUGGESTED, EvidenceState.UNCERTAIN}:
            raise EconomicEvidenceGraphError("new policy links must begin observed, suggested, or uncertain")
        trusted_anchor = (
            None
            if anchor is None
            else self._resolve_anchor(anchor, expected_work_id=policy_work_id, expected_version_id=None)
        )
        item = PolicyLink(
            id=stable_id("plnk", project_id, idempotency_key),
            project_id=project_id,
            research_work_id=research_work_id,
            policy_work_id=policy_work_id,
            relationship_type=relationship_type,
            evidence_anchor=trusted_anchor,
            method=method,
            confidence=confidence,
            state=state,
            created_by=actor_id,
            created_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_policy_link(item, expected_revision=expected_revision)

    def review(
        self,
        project_id: str,
        *,
        actor_id: str,
        target_type: ReviewTargetType,
        target_id: str,
        new_state: EvidenceState,
        reason: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Project:
        """Append an authorized review transition without mutating the proposed value."""
        bundle = self.get(project_id, actor_id=actor_id)
        if _role(bundle, actor_id) not in {ProjectRole.OWNER, ProjectRole.REVIEWER}:
            raise AuthorizationError("evidence approval requires an owner or reviewer role")
        event_id = stable_id("rev", project_id, idempotency_key)
        prior_event = self._repository.get_review_event(event_id)
        if prior_event is not None:
            if (
                prior_event.target_type is target_type
                and prior_event.target_id == target_id
                and prior_event.new_state is new_state
                and prior_event.reviewed_by == actor_id
                and prior_event.reason == reason
            ):
                return bundle.project
            raise ConflictError("review idempotency key was reused for a different decision")
        initial = _initial_review_state(bundle, target_type, target_id)
        history = tuple(
            item for item in bundle.review_events if item.target_type is target_type and item.target_id == target_id
        )
        current = review_state(initial, history)
        if new_state not in _allowed_review_transitions(current):
            raise EconomicEvidenceGraphError(f"review transition from {current.value} to {new_state.value} is invalid")
        event = ReviewEvent(
            id=event_id,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            previous_state=current,
            new_state=new_state,
            reason=reason,
            reviewed_by=actor_id,
            reviewed_at=self._clock.now(),
            idempotency_key=idempotency_key,
        )
        return self._repository.put_review_event(event, expected_revision=expected_revision)

    def trace_policy_links(
        self,
        project_id: str,
        *,
        actor_id: str,
        work_id: str,
    ) -> tuple[PolicyLink, ...]:
        """Return bounded typed adjacency for one research or policy work."""
        bundle = self.get(project_id, actor_id=actor_id)
        return tuple(
            item for item in bundle.policy_links if item.research_work_id == work_id or item.policy_work_id == work_id
        )[:200]

    def compare_table(
        self,
        project_id: str,
        table_id: str,
        *,
        actor_id: str,
    ) -> dict[str, JsonValue]:
        """Return a current, policy-revalidated structured comparison without mutation."""
        bundle = self.get(project_id, actor_id=actor_id)
        table = _find(bundle.evidence_tables, table_id, "evidence table")
        rows = tuple(item for item in bundle.evidence_table_rows if item.table_id == table_id)
        payload, warnings = self._export_payload(bundle, table, rows)
        return {
            "schema_version": PROJECT_EXPORT_SCHEMA_VERSION,
            "project_id": project_id,
            "project_revision": bundle.project.revision,
            "baseline_corpus_snapshot_id": bundle.project.baseline_corpus_snapshot_id,
            "baseline_index_generation_id": bundle.project.baseline_index_generation_id,
            "table": cast(dict[str, JsonValue], to_json_value(table)),
            "rows": cast(list[JsonValue], to_json_value(payload)),
            "warnings": list(warnings),
        }

    def export_table(
        self,
        project_id: str,
        table_id: str,
        *,
        actor_id: str,
        format: ProjectExportFormat,
        expected_revision: int,
        idempotency_key: str,
    ) -> ProjectExport:
        """Export one governed evidence table plus an immutable reproducibility manifest."""
        bundle = self.get(project_id, actor_id=actor_id)
        if _role(bundle, actor_id) not in {ProjectRole.OWNER, ProjectRole.EDITOR, ProjectRole.REVIEWER}:
            raise AuthorizationError("project viewers cannot create exports")
        export_id = stable_id("pexp", project_id, idempotency_key)
        try:
            prior = self._repository.get_project_export(export_id)
        except NotFoundError:
            prior = None
        if prior is not None:
            if (
                prior.project_id == project_id
                and prior.table_id == table_id
                and prior.format is format
                and prior.created_by == actor_id
            ):
                return prior
            raise ConflictError("export idempotency key was reused for different export input")
        table = _find(bundle.evidence_tables, table_id, "evidence table")
        rows = tuple(item for item in bundle.evidence_table_rows if item.table_id == table_id)
        payload, warnings = self._export_payload(bundle, table, rows)
        data, media_type = _render_export(format, payload, table)
        data_ref = self._artifacts.put_bytes(data, media_type=media_type)
        created_at = self._clock.now()
        manifest_identity: dict[str, JsonValue] = {
            "schema_version": PROJECT_EXPORT_SCHEMA_VERSION,
            "project_id": project_id,
            "project_revision": bundle.project.revision,
            "table_id": table_id,
            "format": format.value,
            "baseline_corpus_snapshot_id": bundle.project.baseline_corpus_snapshot_id,
            "baseline_index_generation_id": bundle.project.baseline_index_generation_id,
            "search_run_ids": to_json_value([item.search_run_id for item in bundle.search_runs]),
            "evidence_record_ids": to_json_value(sorted(item.evidence_record_id for item in rows)),
            "review_event_ids": to_json_value(sorted(item.id for item in bundle.review_events)),
            "evidence_dependencies": to_json_value(_evidence_dependencies(payload)),
            "export_id": export_id,
            "data": {
                "uri": data_ref.uri,
                "sha256": data_ref.sha256,
                "media_type": data_ref.media_type,
                "byte_length": data_ref.byte_length,
            },
            "warnings": list(warnings),
            "created_at": to_json_value(created_at),
            "created_by": actor_id,
        }
        manifest_ref = self._artifacts.put_bytes(canonical_json_bytes(manifest_identity), media_type="application/json")
        item = ProjectExport(
            id=export_id,
            project_id=project_id,
            table_id=table_id,
            format=format,
            project_revision=bundle.project.revision,
            data_uri=data_ref.uri,
            data_sha256=data_ref.sha256,
            data_media_type=data_ref.media_type,
            data_byte_length=data_ref.byte_length,
            manifest_uri=manifest_ref.uri,
            manifest_sha256=manifest_ref.sha256,
            manifest_byte_length=manifest_ref.byte_length,
            warnings=warnings,
            created_by=actor_id,
            created_at=created_at,
            idempotency_key=idempotency_key,
        )
        self._repository.put_project_export(item, expected_revision=expected_revision)
        return item

    def export_artifact(self, export_id: str, *, actor_id: str, manifest: bool) -> tuple[ArtifactRef, ProjectExport]:
        """Authorize and resolve a recorded project export artifact."""
        item = self._repository.get_project_export(export_id)
        self.get(item.project_id, actor_id=actor_id)
        ref = ArtifactRef(
            uri=item.manifest_uri if manifest else item.data_uri,
            sha256=item.manifest_sha256 if manifest else item.data_sha256,
            media_type="application/json" if manifest else item.data_media_type,
            byte_length=item.manifest_byte_length if manifest else item.data_byte_length,
        )
        return ref, item

    def _require_editor(self, project_id: str, actor_id: str) -> ProjectBundle:
        """Require a role allowed to create research state."""
        bundle = self.get(project_id, actor_id=actor_id)
        if _role(bundle, actor_id) not in {ProjectRole.OWNER, ProjectRole.EDITOR}:
            raise AuthorizationError("project mutation requires an owner or editor role")
        return bundle

    def _resolve_anchor(
        self,
        item: EvidenceAnchorInput,
        *,
        expected_work_id: str,
        expected_version_id: str | None,
    ) -> ProjectEvidenceAnchor:
        """Validate identity, policy delivery, locator, quote, and content hash."""
        projection = self._search_index.get_evidence(item.evidence_unit_id)
        if projection is None:
            raise NotFoundError("evidence anchor was not found", context={"evidence_unit_id": item.evidence_unit_id})
        if str(projection.get("work_id", "")) != expected_work_id:
            raise EconomicEvidenceGraphError("evidence anchor belongs to a different work")
        if expected_version_id is not None and str(projection.get("document_version_id", "")) != expected_version_id:
            raise EconomicEvidenceGraphError("evidence anchor belongs to a different document version")
        # Retrieval performs the immediate suppression and current source-policy delivery gates.
        snippet = self._retrieval.evidence(item.evidence_unit_id)  # type: ignore[attr-defined]
        quote = item.quote.strip()
        if snippet.text is None or quote not in snippet.text:
            raise EconomicEvidenceGraphError("evidence quote is not present in the policy-deliverable excerpt")
        return ProjectEvidenceAnchor(
            evidence_unit_id=item.evidence_unit_id,
            locator=snippet.locator,
            quote=quote,
            content_sha256=snippet.content_sha256,
        )

    def _export_payload(
        self,
        bundle: ProjectBundle,
        table: EvidenceTable,
        rows: tuple[EvidenceTableRow, ...],
    ) -> tuple[list[dict[str, JsonValue]], tuple[str, ...]]:
        """Build policy-revalidated, state-explicit analytical rows."""
        records = {item.id: item for item in bundle.evidence_records}
        reviews_by_target: dict[str, tuple[ReviewEvent, ...]] = {}
        for field_id in (field.id for record in bundle.evidence_records for field in record.fields):
            reviews_by_target[field_id] = tuple(
                item
                for item in bundle.review_events
                if item.target_type is ReviewTargetType.EVIDENCE_FIELD and item.target_id == field_id
            )
        warnings: set[str] = set()
        output: list[dict[str, JsonValue]] = []
        for row in sorted(rows, key=lambda item: (item.created_at, item.id)):
            record = records[row.evidence_record_id]
            fields = {item.id: item for item in record.fields}
            values: dict[str, JsonValue] = {}
            for cell in row.cells:
                if cell.evidence_field_id is None:
                    values[cell.column_key] = {
                        "display_value": None,
                        "normalized_value": None,
                        "missingness_reason": cell.missingness_reason,
                        "review_state": "not_applicable",
                        "anchors": [],
                    }
                    continue
                field = fields[cell.evidence_field_id]
                deliverable = True
                for anchor in field.anchors:
                    try:
                        snippet = self._retrieval.evidence(anchor.evidence_unit_id)  # type: ignore[attr-defined]
                        deliverable = deliverable and snippet.content_sha256 == anchor.content_sha256
                    except (NotFoundError, PolicyDeniedError):
                        deliverable = False
                if not deliverable:
                    warning = f"evidence field {field.id} is unavailable under current suppression or policy"
                    warnings.add(warning)
                state = review_state(field.state, reviews_by_target[field.id])
                values[cell.column_key] = {
                    "display_value": field.display_value if deliverable else None,
                    "normalized_value": field.normalized_value if deliverable else None,
                    "units": field.units,
                    "applicability": field.applicability,
                    "missingness_reason": field.missingness_reason if deliverable else "unavailable",
                    "review_state": state.value,
                    "lineage_method": field.lineage_method.value,
                    "anchors": (
                        to_json_value(field.anchors)
                        if deliverable
                        else [
                            {
                                "evidence_unit_id": anchor.evidence_unit_id,
                                "locator": anchor.locator,
                                "quote": None,
                                "content_sha256": anchor.content_sha256,
                            }
                            for anchor in field.anchors
                        ]
                    ),
                }
            output.append(
                {
                    "row_id": row.id,
                    "label": row.label,
                    "evidence_record_id": record.id,
                    "work_id": record.work_id,
                    "document_version_id": record.document_version_id,
                    "values": values,
                }
            )
        return output, tuple(sorted(warnings))


def _validate_initial_field_state(item: EvidenceFieldInput) -> None:
    """Keep approvals and terminal states behind the independent review workflow."""
    allowed = {EvidenceState.OBSERVED, EvidenceState.SUGGESTED, EvidenceState.UNCERTAIN}
    if item.state not in allowed:
        raise EconomicEvidenceGraphError("new evidence fields must begin observed, suggested, or uncertain")
    if item.lineage_method is EvidenceLineageMethod.USER_AUTHORED and item.state is EvidenceState.OBSERVED:
        raise EconomicEvidenceGraphError("user-authored evidence cannot be labeled as source-observed")


def _initial_review_state(bundle: ProjectBundle, target_type: ReviewTargetType, target_id: str) -> EvidenceState:
    """Resolve the immutable proposal state for a review target."""
    if target_type is ReviewTargetType.EVIDENCE_FIELD:
        for record in bundle.evidence_records:
            for field in record.fields:
                if field.id == target_id:
                    return field.state
    else:
        for link in bundle.policy_links:
            if link.id == target_id:
                return link.state
    raise NotFoundError("review target was not found", context={"target_id": target_id})


def _allowed_review_transitions(state: EvidenceState) -> set[EvidenceState]:
    """Return explicit Step 4 review transitions."""
    return {
        EvidenceState.OBSERVED: {
            EvidenceState.APPROVED,
            EvidenceState.REJECTED,
            EvidenceState.UNCERTAIN,
            EvidenceState.SUPERSEDED,
        },
        EvidenceState.SUGGESTED: {
            EvidenceState.APPROVED,
            EvidenceState.REJECTED,
            EvidenceState.UNCERTAIN,
            EvidenceState.SUPERSEDED,
        },
        EvidenceState.UNCERTAIN: {
            EvidenceState.APPROVED,
            EvidenceState.REJECTED,
            EvidenceState.SUPERSEDED,
        },
        EvidenceState.APPROVED: {EvidenceState.SUPERSEDED},
        EvidenceState.REJECTED: set(),
        EvidenceState.SUPERSEDED: set(),
    }[state]


def _role(bundle: ProjectBundle, actor_id: str) -> ProjectRole:
    """Return a collaborator role without leaking project state to outsiders."""
    _require_actor(actor_id)
    member = next((item for item in bundle.members if item.actor_id == actor_id), None)
    if member is None:
        raise AuthorizationError("actor is not a member of this project")
    return member.role


def _require_actor(actor_id: str) -> None:
    """Require explicit actor attribution."""
    if not actor_id.strip() or len(actor_id) > 255:
        raise EconomicEvidenceGraphError("actor identifier must contain 1 to 255 characters")


def _find(items: tuple[TItem, ...], identifier: str, label: str) -> TItem:
    """Find a dataclass with an id attribute without widening repository APIs."""
    found = next((item for item in items if getattr(item, "id", None) == identifier), None)
    if found is None:
        raise NotFoundError(f"{label} was not found", context={"id": identifier})
    return found


def _render_export(
    format: ProjectExportFormat,
    rows: list[dict[str, JsonValue]],
    table: EvidenceTable,
) -> tuple[bytes, str]:
    """Serialize an evidence table using only open, deterministic formats."""
    if format is ProjectExportFormat.JSON:
        return canonical_json_bytes(rows), "application/json"
    headers = ["label", "work_id", "document_version_id"]
    for column in table.columns:
        headers.extend(
            (
                column.key,
                f"{column.key}__review_state",
                f"{column.key}__missingness",
                f"{column.key}__evidence_ids",
            )
        )
    flat_rows = [_flatten_export_row(item, table) for item in rows]
    if format is ProjectExportFormat.CSV:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: _csv_safe(value) for key, value in row.items()} for row in flat_rows)
        return output.getvalue().encode("utf-8"), "text/csv"
    lines = [
        "| " + " | ".join(_markdown_escape(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in flat_rows:
        lines.append("| " + " | ".join(_markdown_escape(str(row[item])) for item in headers) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8"), "text/markdown"


def _flatten_export_row(item: dict[str, JsonValue], table: EvidenceTable) -> dict[str, str]:
    """Flatten the structured JSON representation for tabular formats."""
    values = cast(dict[str, dict[str, JsonValue]], item["values"])
    result = {
        "label": str(item["label"]),
        "work_id": str(item["work_id"]),
        "document_version_id": str(item["document_version_id"]),
    }
    for column in table.columns:
        cell = values[column.key]
        display = cell.get("display_value")
        result[column.key] = str(display if display is not None else f"[{cell.get('missingness_reason')}]")
        result[f"{column.key}__review_state"] = str(cell.get("review_state"))
        result[f"{column.key}__missingness"] = str(cell.get("missingness_reason") or "")
        anchors = cast(list[dict[str, JsonValue]], cell.get("anchors", []))
        result[f"{column.key}__evidence_ids"] = ";".join(str(anchor["evidence_unit_id"]) for anchor in anchors)
    return result


def _evidence_dependencies(rows: list[dict[str, JsonValue]]) -> list[JsonValue]:
    """Enumerate immutable evidence identifiers and hashes used by an export."""
    dependencies: set[tuple[str, str]] = set()
    for row in rows:
        values = cast(dict[str, dict[str, JsonValue]], row["values"])
        for cell in values.values():
            for anchor in cast(list[dict[str, JsonValue]], cell.get("anchors", [])):
                dependencies.add((str(anchor["evidence_unit_id"]), str(anchor["content_sha256"])))
    return [
        {"evidence_unit_id": evidence_id, "content_sha256": content_hash}
        for evidence_id, content_hash in sorted(dependencies)
    ]


def _csv_safe(value: str) -> str:
    """Prevent spreadsheet formula interpretation without changing JSON exports."""
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def _markdown_escape(value: str) -> str:
    """Escape the only Markdown-table structural characters."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
