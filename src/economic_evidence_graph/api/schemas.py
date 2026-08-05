"""Stable API request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from economic_evidence_graph.domain import EvidenceBundle, EvidenceSnippet, Job, SearchResponse, SearchRun
from economic_evidence_graph.domain.models import ScalarValue
from economic_evidence_graph.serialization import to_json_value

API_SCHEMA_VERSION = "eeg.api.v1"


class StrictModel(BaseModel):
    """Base schema rejecting accidental undocumented fields."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"additionalProperties": False})


class ExternalIdentifierResource(StrictModel):
    """External identifier exposed with a work."""

    id: str
    scheme: str
    value: str


class WorkResource(StrictModel):
    """Canonical work representation."""

    id: str
    title: str
    work_type: str
    authors: list[str]
    identifiers: list[ExternalIdentifierResource]


class DocumentVersionResource(StrictModel):
    """Version and historical-eligibility representation."""

    id: str
    work_id: str
    raw_asset_id: str
    version_label: str
    asserted_at: date | None
    first_observed_at: datetime
    eligible_from: datetime | None
    temporal_safety_class: str


class ManifestationResource(StrictModel):
    """Source manifestation representation."""

    id: str
    document_version_id: str
    source_id: str
    source_identifier: str
    source_url: str
    access_status: str
    license_uri: str | None


class EvidenceUnitResource(StrictModel):
    """Page- and section-anchored evidence passage."""

    id: str
    extraction_artifact_id: str
    ordinal: int
    section_path: list[str]
    page_start: int
    page_end: int
    text: str
    temporal_safety_class: str


class EvidenceAnchorResource(StrictModel):
    """Exact evidence support locator."""

    evidence_unit_id: str
    locator: str
    quote: str


class EvidenceFieldResource(StrictModel):
    """Typed, reviewable field revision."""

    id: str
    field_name: str
    display_value: str
    normalized_value: ScalarValue
    units: str | None
    applicability: str
    state: str
    lineage_method: str
    confidence: float
    anchors: list[EvidenceAnchorResource]


class EvidenceRecordResource(StrictModel):
    """Economics-specific evidence record."""

    id: str
    work_id: str
    document_version_id: str
    record_type: str
    schema_version: str
    fields: list[EvidenceFieldResource]


class RetrievalResource(StrictModel):
    """Acquisition provenance exposed to clients."""

    id: str
    source_id: str
    source_policy_version_id: str
    requested_url: str
    resolved_url: str
    retrieved_at: datetime
    http_status: int
    media_type: str
    content_sha256: str
    collector_version: str


class ProvenanceResource(StrictModel):
    """Lineage identifiers required to reproduce a work response."""

    snapshot_id: str
    snapshot_manifest_sha256: str
    source_ids: list[str]
    source_policy_version_ids: list[str]
    retrievals: list[RetrievalResource]
    raw_asset_ids: list[str]
    extraction_artifact_ids: list[str]


class WorkResponse(StrictModel):
    """Provenance-complete work API response."""

    schema_version: str = API_SCHEMA_VERSION
    work: WorkResource
    document_versions: list[DocumentVersionResource]
    manifestations: list[ManifestationResource]
    evidence_units: list[EvidenceUnitResource]
    evidence_records: list[EvidenceRecordResource]
    provenance: ProvenanceResource

    @classmethod
    def from_bundle(cls, bundle: EvidenceBundle) -> WorkResponse:
        """Map a validated domain bundle into the public response schema."""
        return cls(
            work=WorkResource(
                id=bundle.work.id,
                title=bundle.work.title,
                work_type=bundle.work.work_type.value,
                authors=list(bundle.work.authors),
                identifiers=[
                    ExternalIdentifierResource(id=item.id, scheme=item.scheme, value=item.value)
                    for item in bundle.work.identifiers
                ],
            ),
            document_versions=[
                DocumentVersionResource(
                    id=item.id,
                    work_id=item.work_id,
                    raw_asset_id=item.raw_asset_id,
                    version_label=item.version_label,
                    asserted_at=item.asserted_at,
                    first_observed_at=item.first_observed_at,
                    eligible_from=item.eligible_from,
                    temporal_safety_class=item.temporal_safety_class.value,
                )
                for item in bundle.document_versions
            ],
            manifestations=[
                ManifestationResource(
                    id=item.id,
                    document_version_id=item.document_version_id,
                    source_id=item.source_id,
                    source_identifier=item.source_identifier,
                    source_url=item.source_url,
                    access_status=item.access_status.value,
                    license_uri=item.license_uri,
                )
                for item in bundle.manifestations
            ],
            evidence_units=[
                EvidenceUnitResource(
                    id=item.id,
                    extraction_artifact_id=item.extraction_artifact_id,
                    ordinal=item.ordinal,
                    section_path=list(item.section_path),
                    page_start=item.page_start,
                    page_end=item.page_end,
                    text=item.text,
                    temporal_safety_class=item.temporal_safety_class.value,
                )
                for item in bundle.evidence_units
            ],
            evidence_records=[_evidence_record(item) for item in bundle.evidence_records],
            provenance=ProvenanceResource(
                snapshot_id=bundle.snapshot.id,
                snapshot_manifest_sha256=bundle.snapshot.manifest_sha256,
                source_ids=sorted(source.id for source in bundle.sources),
                source_policy_version_ids=sorted(policy.id for policy in bundle.source_policies),
                retrievals=[
                    RetrievalResource(
                        id=item.id,
                        source_id=item.source_id,
                        source_policy_version_id=item.source_policy_version_id,
                        requested_url=item.requested_url,
                        resolved_url=item.resolved_url,
                        retrieved_at=item.retrieved_at,
                        http_status=item.http_status,
                        media_type=item.media_type,
                        content_sha256=item.content_sha256,
                        collector_version=item.collector_version,
                    )
                    for item in bundle.retrieval_events
                ],
                raw_asset_ids=sorted(item.id for item in bundle.raw_assets),
                extraction_artifact_ids=sorted(item.id for item in bundle.extraction_artifacts),
            ),
        )


class ExportCreateRequest(StrictModel):
    """Request body for a reproducible evidence-pack job."""

    work_ids: list[str] = Field(min_length=1, max_length=100)
    snapshot_id: str | None = None
    format: Literal["jsonl"] = "jsonl"
    idempotency_key: str = Field(min_length=1, max_length=255)


class JobResource(StrictModel):
    """Durable job state returned by the API."""

    id: str
    kind: str
    state: str
    idempotency_key: str
    input: dict[str, ScalarValue]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    output: dict[str, ScalarValue] | None
    error_code: str | None
    error_message: str | None

    @classmethod
    def from_domain(cls, item: Job) -> JobResource:
        """Map an immutable job into its public representation."""
        return cls(
            id=item.id,
            kind=item.kind,
            state=item.state.value,
            idempotency_key=item.idempotency_key,
            input=dict(item.input),
            created_at=item.created_at,
            started_at=item.started_at,
            finished_at=item.finished_at,
            output=None if item.output is None else dict(item.output),
            error_code=item.error_code,
            error_message=item.error_message,
        )


class HealthResponse(StrictModel):
    """Liveness or readiness state."""

    status: Literal["ok"] = "ok"
    profile: Literal["local"] = "local"


class SearchFiltersRequest(StrictModel):
    """Typed search selection filters."""

    source_codes: list[str] = Field(default_factory=list, max_length=20)
    published_from: date | None = None
    published_to: date | None = None


class HistoricalCutoffRequest(StrictModel):
    """Strict historical eligibility boundary."""

    at: datetime
    mode: Literal["strict"] = "strict"


class SearchCreateRequest(StrictModel):
    """Hybrid literature-search request."""

    query: str = Field(min_length=1, max_length=500)
    modes: list[Literal["lexical", "semantic", "structured"]] = Field(
        default_factory=lambda: ["lexical", "semantic"], min_length=1, max_length=3
    )
    filters: SearchFiltersRequest = Field(default_factory=SearchFiltersRequest)
    historical_cutoff: HistoricalCutoffRequest | None = None
    collapse_versions: bool = True
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=2048)
    save_run: bool = False


class ChannelContributionResource(StrictModel):
    """One explainable fusion contribution."""

    channel: str
    rank: int
    raw_score: float
    contribution: float


class TemporalResultResource(StrictModel):
    """Historical-eligibility explanation."""

    cutoff: datetime | None
    eligible: bool
    eligible_from: datetime
    date_basis: str
    date_evidence: str
    uncertainty: str | None
    safety_class: str


class EvidenceSnippetResource(StrictModel):
    """Policy-limited excerpt with exact acquisition and extraction lineage."""

    evidence_unit_id: str
    normalized_document_id: str
    acquired_asset_id: str
    text: str | None
    access_mode: str
    section_path: list[str]
    locator: str
    page_start: int | None
    page_end: int | None
    content_sha256: str
    text_sha256: str
    retrieved_at: datetime
    parser_name: str
    parser_version: str
    extraction_schema_version: str
    warnings: list[str]

    @classmethod
    def from_domain(cls, item: EvidenceSnippet) -> EvidenceSnippetResource:
        """Map a validated evidence excerpt."""
        return cls.model_validate(to_json_value(item))


class SearchResultResource(StrictModel):
    """One ranked, version-collapsed literature result."""

    rank: int
    score: float
    work_id: str
    document_version_id: str
    source_record_id: str
    title: str
    authors: list[str]
    published_at: date | None
    source_code: str
    source_identifier: str
    source_url: str
    policy_version: int
    alternative_version_count: int
    version_selection_reason: str
    contributions: list[ChannelContributionResource]
    matched_phrases: list[str]
    evidence: EvidenceSnippetResource | None
    temporal: TemporalResultResource
    warnings: list[str]


class SearchResponseResource(StrictModel):
    """Reproducible hybrid search response."""

    schema_version: str = API_SCHEMA_VERSION
    query: str
    parsed_terms: list[str]
    results: list[SearchResultResource]
    index_generation_id: str
    corpus_snapshot_id: str
    retrieval_config_id: str
    modes_used: list[str]
    omitted_channels: list[str]
    warnings: list[str]
    next_cursor: str | None
    search_run_id: str | None

    @classmethod
    def from_domain(cls, item: SearchResponse) -> SearchResponseResource:
        """Map a validated search response."""
        return cls.model_validate(to_json_value(item))


class SearchRunResource(StrictModel):
    """Saved request and exact response manifest."""

    id: str
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    index_generation_id: str
    query_sha256: str
    created_at: datetime

    @classmethod
    def from_domain(cls, item: SearchRun) -> SearchRunResource:
        """Map an immutable saved search run."""
        return cls.model_validate(to_json_value(item))


class ProblemDetail(StrictModel):
    """RFC 9457-compatible application error response."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    retryable: bool = False
    context: dict[str, str] = Field(default_factory=dict)
    errors: list[dict[str, object]] | None = None


def _evidence_record(item: object) -> EvidenceRecordResource:
    """Map a domain evidence record without exposing persistence details."""
    from economic_evidence_graph.domain import EvidenceRecord

    record = item if isinstance(item, EvidenceRecord) else None
    if record is None:  # pragma: no cover - protected by the caller's type
        raise TypeError("expected EvidenceRecord")
    return EvidenceRecordResource(
        id=record.id,
        work_id=record.work_id,
        document_version_id=record.document_version_id,
        record_type=record.record_type,
        schema_version=record.schema_version,
        fields=[
            EvidenceFieldResource(
                id=item_field.id,
                field_name=item_field.field_name,
                display_value=item_field.display_value,
                normalized_value=item_field.normalized_value,
                units=item_field.units,
                applicability=item_field.applicability,
                state=item_field.state.value,
                lineage_method=item_field.lineage_method,
                confidence=item_field.confidence,
                anchors=[
                    EvidenceAnchorResource(
                        evidence_unit_id=anchor.evidence_unit_id,
                        locator=anchor.locator,
                        quote=anchor.quote,
                    )
                    for anchor in item_field.anchors
                ],
            )
            for item_field in record.fields
        ],
    )
