"""Step 4 project, review, evidence-table, and policy-link API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from economic_evidence_graph.api.schemas import API_SCHEMA_VERSION, StrictModel
from economic_evidence_graph.domain import Project, ProjectBundle, ProjectExport
from economic_evidence_graph.domain.models import ScalarValue
from economic_evidence_graph.serialization import to_json_value


class ProjectCreateRequest(StrictModel):
    """Create a collaborative research workspace."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ProjectOperationRequest(StrictModel):
    """Optimistic and idempotent project mutation fields."""

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ProjectResource(StrictModel):
    """Project identity, reproducibility baseline, and optimistic revision."""

    id: str
    name: str
    description: str
    created_by: str
    created_at: datetime
    baseline_corpus_snapshot_id: str | None
    baseline_index_generation_id: str | None
    idempotency_key: str
    revision: int

    @classmethod
    def from_domain(cls, item: Project) -> ProjectResource:
        """Map one project."""
        return cls.model_validate(to_json_value(item))


class ProjectMemberCreateRequest(ProjectOperationRequest):
    """Add a collaborator role."""

    actor_id: str = Field(min_length=1, max_length=255)
    role: Literal["owner", "editor", "reviewer", "viewer"]


class ProjectSearchRunCreateRequest(ProjectOperationRequest):
    """Attach an existing immutable search run."""

    search_run_id: str = Field(min_length=1, max_length=64)


class ScreeningCreateRequest(ProjectOperationRequest):
    """Append one controlled screening decision."""

    work_id: str = Field(min_length=1, max_length=64)
    document_version_id: str | None = Field(default=None, max_length=64)
    outcome: Literal["include", "exclude", "maybe"]
    reason_code: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2_000)


class ProjectNoteCreateRequest(ProjectOperationRequest):
    """Append a project-object note."""

    target_type: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    target_id: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=10_000)


class EvidenceAnchorCreateRequest(StrictModel):
    """Quote to resolve against a governed evidence unit."""

    evidence_unit_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=600)


class EvidenceFieldCreateRequest(StrictModel):
    """Typed project evidence proposal."""

    field_name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    display_value: str = Field(default="", max_length=2_000)
    normalized_value: ScalarValue = None
    units: str | None = Field(default=None, max_length=128)
    applicability: str = Field(min_length=1, max_length=2_000)
    missingness_reason: str | None = Field(default=None, max_length=64)
    state: Literal["observed", "suggested", "uncertain"]
    lineage_method: Literal["observed_transcription", "deterministic_extraction", "user_authored"]
    confidence: float = Field(ge=0, le=1)
    anchors: list[EvidenceAnchorCreateRequest] = Field(default_factory=list, max_length=10)


class EvidenceRecordCreateRequest(ProjectOperationRequest):
    """Create one economics-specific evidence record."""

    work_id: str = Field(min_length=1, max_length=64)
    document_version_id: str = Field(min_length=1, max_length=64)
    record_type: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    fields: list[EvidenceFieldCreateRequest] = Field(min_length=1, max_length=50)


class EvidenceTableColumnRequest(StrictModel):
    """One declared comparison column."""

    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=200)
    value_type: Literal["text", "integer", "number", "boolean"]
    units: str | None = Field(default=None, max_length=128)


class EvidenceTableCreateRequest(ProjectOperationRequest):
    """Create a declared comparison schema."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    columns: list[EvidenceTableColumnRequest] = Field(min_length=1, max_length=50)


class EvidenceTableCellRequest(StrictModel):
    """Reference one field or declare one missingness reason."""

    column_key: str = Field(min_length=2, max_length=64)
    evidence_field_id: str | None = Field(default=None, max_length=64)
    missingness_reason: str | None = Field(default=None, max_length=64)


class EvidenceTableRowCreateRequest(ProjectOperationRequest):
    """Add one fully explicit study comparison row."""

    evidence_record_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=500)
    cells: list[EvidenceTableCellRequest] = Field(min_length=1, max_length=50)


class PolicyLinkCreateRequest(ProjectOperationRequest):
    """Create a semantically typed research-to-policy relationship."""

    research_work_id: str = Field(min_length=1, max_length=64)
    policy_work_id: str = Field(min_length=1, max_length=64)
    relationship_type: Literal["direct_citation", "explicit_mention", "shared_source", "topical_suggestion"]
    anchor: EvidenceAnchorCreateRequest | None = None
    method: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    confidence: float = Field(ge=0, le=1)
    state: Literal["observed", "suggested", "uncertain"]


class ReviewCreateRequest(ProjectOperationRequest):
    """Append one independent review transition."""

    target_type: Literal["evidence_field", "policy_link"]
    target_id: str = Field(min_length=1, max_length=64)
    new_state: Literal["approved", "rejected", "uncertain", "superseded"]
    reason: str = Field(min_length=1, max_length=2_000)


class ProjectExportCreateRequest(ProjectOperationRequest):
    """Create a governed, reproducible evidence-table export."""

    format: Literal["json", "csv", "markdown"] = "json"


class ProjectExportResource(StrictModel):
    """Checksum-addressed project export metadata."""

    id: str
    project_id: str
    table_id: str
    format: str
    project_revision: int
    data_uri: str
    data_sha256: str
    data_media_type: str
    data_byte_length: int
    manifest_uri: str
    manifest_sha256: str
    manifest_byte_length: int
    warnings: list[str]
    created_by: str
    created_at: datetime
    idempotency_key: str

    @classmethod
    def from_domain(cls, item: ProjectExport) -> ProjectExportResource:
        """Map one immutable export."""
        return cls.model_validate(to_json_value(item))


class ProjectBundleResource(StrictModel):
    """Complete project state with explicit append-only history."""

    schema_version: str = API_SCHEMA_VERSION
    project: dict[str, object]
    members: list[dict[str, object]]
    search_runs: list[dict[str, object]]
    screening_events: list[dict[str, object]]
    notes: list[dict[str, object]]
    evidence_records: list[dict[str, object]]
    evidence_tables: list[dict[str, object]]
    evidence_table_rows: list[dict[str, object]]
    policy_links: list[dict[str, object]]
    review_events: list[dict[str, object]]
    exports: list[dict[str, object]]

    @classmethod
    def from_domain(cls, item: ProjectBundle) -> ProjectBundleResource:
        """Map a complete authorized handoff."""
        payload = to_json_value(item)
        if not isinstance(payload, dict):  # pragma: no cover - dataclass serialization contract
            raise TypeError("expected project bundle object")
        return cls.model_validate(payload)
