"""Collaborative projects, reviewable evidence, and policy-link contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from economic_evidence_graph.domain.models import EvidenceState, ScalarValue
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProjectRole(StrEnum):
    """Authorization role granted to a project collaborator."""

    OWNER = "owner"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class ScreeningOutcome(StrEnum):
    """One append-only literature-screening decision."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    MAYBE = "maybe"


class EvidenceLineageMethod(StrEnum):
    """How a project evidence value entered the system."""

    OBSERVED_TRANSCRIPTION = "observed_transcription"
    DETERMINISTIC_EXTRACTION = "deterministic_extraction"
    USER_AUTHORED = "user_authored"


class PolicyLinkType(StrEnum):
    """Semantically distinct research-to-policy relationships."""

    DIRECT_CITATION = "direct_citation"
    EXPLICIT_MENTION = "explicit_mention"
    SHARED_SOURCE = "shared_source"
    TOPICAL_SUGGESTION = "topical_suggestion"


class ReviewTargetType(StrEnum):
    """Project object that can receive an append-only review event."""

    EVIDENCE_FIELD = "evidence_field"
    POLICY_LINK = "policy_link"


class ProjectExportFormat(StrEnum):
    """Open, document-friendly evidence-table export formats."""

    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class Project:
    """Collaborative research workspace with a reproducible starting point."""

    id: str
    name: str
    description: str
    created_by: str
    created_at: datetime
    baseline_corpus_snapshot_id: str | None
    baseline_index_generation_id: str | None
    idempotency_key: str
    revision: int = 1

    def __post_init__(self) -> None:
        _require_text(self.id, "project id")
        _require_text(self.name, "project name")
        _require_text(self.created_by, "project creator")
        _require_text(self.idempotency_key, "project idempotency key")
        _require_utc(self.created_at, "project created_at")
        if len(self.name) > 200 or len(self.description) > 4_000:
            raise EconomicEvidenceGraphError("project name or description exceeds its size limit")
        if self.revision < 1:
            raise EconomicEvidenceGraphError("project revision must be positive")
        if (self.baseline_corpus_snapshot_id is None) != (self.baseline_index_generation_id is None):
            raise EconomicEvidenceGraphError("project baseline snapshot and index generation must be paired")


@dataclass(frozen=True, slots=True)
class ProjectMember:
    """One attributed collaborator role."""

    id: str
    project_id: str
    actor_id: str
    role: ProjectRole
    added_by: str
    added_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.added_by, self.added_at, self.idempotency_key)
        _require_text(self.actor_id, "project member actor")


@dataclass(frozen=True, slots=True)
class ProjectSearchRun:
    """Immutable attachment of a reproducible search run to a project."""

    id: str
    project_id: str
    search_run_id: str
    saved_by: str
    saved_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.saved_by, self.saved_at, self.idempotency_key)
        _require_text(self.search_run_id, "saved search run id")


@dataclass(frozen=True, slots=True)
class ScreeningEvent:
    """Append-only screening decision; later events compensate rather than overwrite."""

    id: str
    project_id: str
    work_id: str
    document_version_id: str | None
    outcome: ScreeningOutcome
    reason_code: str | None
    note: str | None
    decided_by: str
    decided_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.decided_by, self.decided_at, self.idempotency_key)
        _require_text(self.work_id, "screening work id")
        if self.outcome is ScreeningOutcome.EXCLUDE and self.reason_code is None:
            raise EconomicEvidenceGraphError("excluded works require a controlled reason code")
        if self.reason_code is not None:
            _require_code(self.reason_code, "screening reason code")
        if self.note is not None and len(self.note) > 2_000:
            raise EconomicEvidenceGraphError("screening note exceeds 2000 characters")


@dataclass(frozen=True, slots=True)
class ProjectNote:
    """Immutable note attached to a stable project object."""

    id: str
    project_id: str
    target_type: str
    target_id: str
    body: str
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_code(self.target_type, "note target type")
        _require_text(self.target_id, "note target id")
        _require_text(self.body, "note body")
        if len(self.body) > 10_000:
            raise EconomicEvidenceGraphError("note body exceeds 10000 characters")


@dataclass(frozen=True, slots=True)
class ProjectEvidenceAnchor:
    """Exact, policy-checked source support for one analytical value."""

    evidence_unit_id: str
    locator: str
    quote: str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.evidence_unit_id, "project evidence unit id")
        _require_text(self.locator, "project evidence locator")
        _require_text(self.quote, "project evidence quote")
        if len(self.quote) > 600:
            raise EconomicEvidenceGraphError("project evidence quote exceeds the delivery limit")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise EconomicEvidenceGraphError("project evidence content hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class ProjectEvidenceField:
    """Typed field revision with explicit review state and source lineage."""

    id: str
    field_name: str
    display_value: str
    normalized_value: ScalarValue
    units: str | None
    applicability: str
    missingness_reason: str | None
    state: EvidenceState
    lineage_method: EvidenceLineageMethod
    confidence: float
    anchors: tuple[ProjectEvidenceAnchor, ...]
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.id, "project evidence field id")
        _require_code(self.field_name, "project evidence field name")
        _require_text(self.applicability, "project evidence applicability")
        _require_text(self.created_by, "project evidence field creator")
        _require_utc(self.created_at, "project evidence field created_at")
        if not 0 <= self.confidence <= 1:
            raise EconomicEvidenceGraphError("project evidence confidence must be between zero and one")
        if self.missingness_reason is None and not self.display_value.strip():
            raise EconomicEvidenceGraphError("present evidence fields require a display value")
        if self.missingness_reason is not None:
            _require_code(self.missingness_reason, "project evidence missingness reason")
        if self.lineage_method is EvidenceLineageMethod.USER_AUTHORED:
            if self.anchors:
                raise EconomicEvidenceGraphError("user-authored evidence fields must not imply source anchors")
        elif not self.anchors:
            raise EconomicEvidenceGraphError("observed or extracted evidence fields require source anchors")
        if len({anchor.evidence_unit_id for anchor in self.anchors}) != len(self.anchors):
            raise EconomicEvidenceGraphError("project evidence anchors must be unique")


@dataclass(frozen=True, slots=True)
class ProjectEvidenceRecord:
    """Project-owned, reviewable economic evidence observation."""

    id: str
    project_id: str
    work_id: str
    document_version_id: str
    record_type: str
    schema_version: str
    fields: tuple[ProjectEvidenceField, ...]
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_text(self.work_id, "project evidence work id")
        _require_text(self.document_version_id, "project evidence version id")
        _require_code(self.record_type, "project evidence record type")
        _require_text(self.schema_version, "project evidence schema version")
        if (
            not self.fields
            or len({item.id for item in self.fields}) != len(self.fields)
            or len({item.field_name for item in self.fields}) != len(self.fields)
        ):
            raise EconomicEvidenceGraphError("project evidence records require unique fields")


@dataclass(frozen=True, slots=True)
class EvidenceTableColumn:
    """Declared comparison column, independent of any one populated cell."""

    key: str
    label: str
    value_type: str
    units: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.key, "evidence table column key")
        _require_text(self.label, "evidence table column label")
        if self.value_type not in {"text", "integer", "number", "boolean"}:
            raise EconomicEvidenceGraphError("evidence table column value type is unsupported")


@dataclass(frozen=True, slots=True)
class EvidenceTable:
    """Versioned schema for a user-visible study comparison."""

    id: str
    project_id: str
    name: str
    description: str
    columns: tuple[EvidenceTableColumn, ...]
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_text(self.name, "evidence table name")
        if not self.columns or len({item.key for item in self.columns}) != len(self.columns):
            raise EconomicEvidenceGraphError("evidence table requires unique columns")
        if len(self.columns) > 50:
            raise EconomicEvidenceGraphError("evidence tables support at most 50 columns")


@dataclass(frozen=True, slots=True)
class EvidenceTableCell:
    """Reference to a reviewed field or an explicit missingness state."""

    column_key: str
    evidence_field_id: str | None
    missingness_reason: str | None

    def __post_init__(self) -> None:
        _require_code(self.column_key, "evidence table cell column")
        if (self.evidence_field_id is None) == (self.missingness_reason is None):
            raise EconomicEvidenceGraphError("evidence table cells require exactly one value or missingness reason")
        if self.missingness_reason is not None:
            _require_code(self.missingness_reason, "evidence table cell missingness reason")


@dataclass(frozen=True, slots=True)
class EvidenceTableRow:
    """One study/version comparison row linked to evidence field revisions."""

    id: str
    project_id: str
    table_id: str
    evidence_record_id: str
    label: str
    cells: tuple[EvidenceTableCell, ...]
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_text(self.table_id, "evidence table row table id")
        _require_text(self.evidence_record_id, "evidence table row record id")
        _require_text(self.label, "evidence table row label")
        if not self.cells or len({item.column_key for item in self.cells}) != len(self.cells):
            raise EconomicEvidenceGraphError("evidence table rows require unique populated cells")


@dataclass(frozen=True, slots=True)
class PolicyLink:
    """Typed, reviewable relationship from research into a policy document."""

    id: str
    project_id: str
    research_work_id: str
    policy_work_id: str
    relationship_type: PolicyLinkType
    evidence_anchor: ProjectEvidenceAnchor | None
    method: str
    confidence: float
    state: EvidenceState
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_text(self.research_work_id, "policy link research work id")
        _require_text(self.policy_work_id, "policy link policy work id")
        _require_code(self.method, "policy link method")
        if self.research_work_id == self.policy_work_id:
            raise EconomicEvidenceGraphError("policy links require distinct research and policy works")
        if not 0 <= self.confidence <= 1:
            raise EconomicEvidenceGraphError("policy link confidence must be between zero and one")
        if (
            self.relationship_type in {PolicyLinkType.DIRECT_CITATION, PolicyLinkType.EXPLICIT_MENTION}
            and self.evidence_anchor is None
        ):
            raise EconomicEvidenceGraphError("direct citations and explicit mentions require exact evidence")


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """Append-only review transition with actor, reason, and prior state."""

    id: str
    project_id: str
    target_type: ReviewTargetType
    target_id: str
    previous_state: EvidenceState
    new_state: EvidenceState
    reason: str
    reviewed_by: str
    reviewed_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.reviewed_by, self.reviewed_at, self.idempotency_key)
        _require_text(self.target_id, "review target id")
        _require_text(self.reason, "review reason")
        if self.previous_state is self.new_state:
            raise EconomicEvidenceGraphError("review events must change state")


@dataclass(frozen=True, slots=True)
class ProjectExport:
    """Immutable, checksum-addressed evidence-table export and manifest."""

    id: str
    project_id: str
    table_id: str
    format: ProjectExportFormat
    project_revision: int
    data_uri: str
    data_sha256: str
    data_media_type: str
    data_byte_length: int
    manifest_uri: str
    manifest_sha256: str
    manifest_byte_length: int
    warnings: tuple[str, ...]
    created_by: str
    created_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_project_item(self.id, self.project_id, self.created_by, self.created_at, self.idempotency_key)
        _require_text(self.table_id, "project export table id")
        _require_text(self.data_uri, "project export data URI")
        _require_text(self.manifest_uri, "project export manifest URI")
        if not _SHA256_PATTERN.fullmatch(self.data_sha256) or not _SHA256_PATTERN.fullmatch(self.manifest_sha256):
            raise EconomicEvidenceGraphError("project export hashes must be SHA-256")
        if self.project_revision < 1 or self.data_byte_length < 0 or self.manifest_byte_length < 0:
            raise EconomicEvidenceGraphError("project export revision or byte length is invalid")


@dataclass(frozen=True, slots=True)
class ProjectBundle:
    """Complete project handoff state with immutable history."""

    project: Project
    members: tuple[ProjectMember, ...]
    search_runs: tuple[ProjectSearchRun, ...]
    screening_events: tuple[ScreeningEvent, ...]
    notes: tuple[ProjectNote, ...]
    evidence_records: tuple[ProjectEvidenceRecord, ...]
    evidence_tables: tuple[EvidenceTable, ...]
    evidence_table_rows: tuple[EvidenceTableRow, ...]
    policy_links: tuple[PolicyLink, ...]
    review_events: tuple[ReviewEvent, ...]
    exports: tuple[ProjectExport, ...]

    def __post_init__(self) -> None:
        items = (
            *self.members,
            *self.search_runs,
            *self.screening_events,
            *self.notes,
            *self.evidence_records,
            *self.evidence_tables,
            *self.evidence_table_rows,
            *self.policy_links,
            *self.review_events,
            *self.exports,
        )
        if any(item.project_id != self.project.id for item in items):
            raise EconomicEvidenceGraphError("project bundle contains cross-project state")


def review_state(initial: EvidenceState, events: tuple[ReviewEvent, ...]) -> EvidenceState:
    """Fold a validated review history into its effective current state."""
    current = initial
    for event in sorted(events, key=lambda item: (item.reviewed_at, item.id)):
        if event.previous_state is not current:
            raise EconomicEvidenceGraphError("review history contains a broken state transition")
        current = event.new_state
    return current


def _require_project_item(identifier: str, project_id: str, actor_id: str, at: datetime, key: str) -> None:
    """Validate identity and attribution shared by project mutations."""
    for value, label in (
        (identifier, "project item id"),
        (project_id, "project item project id"),
        (actor_id, "project item actor"),
        (key, "project item idempotency key"),
    ):
        _require_text(value, label)
    _require_utc(at, "project item timestamp")


def _require_text(value: str, field_name: str) -> None:
    """Require a nonblank string."""
    if not value.strip():
        raise EconomicEvidenceGraphError(f"{field_name} must not be blank")


def _require_code(value: str, field_name: str) -> None:
    """Require a lowercase stable machine code."""
    if not _CODE_PATTERN.fullmatch(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be a lowercase machine code")


def _require_utc(value: datetime, field_name: str) -> None:
    """Require a timezone-aware UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be timezone-aware UTC")
