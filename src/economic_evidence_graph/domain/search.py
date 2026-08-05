"""Immutable search, indexing, and temporal-retrieval contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from economic_evidence_graph.domain.corpus import AcquiredAsset, NormalizedDocument, SourceMetadataRecord
from economic_evidence_graph.domain.models import TemporalSafetyClass
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SearchMode(StrEnum):
    """Candidate channel requested by a literature search."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    STRUCTURED = "structured"


class HistoricalMode(StrEnum):
    """Historical eligibility policy requested by a search."""

    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class CorpusSearchRecord:
    """One source observation plus every acquired and normalized representation."""

    metadata: SourceMetadataRecord
    assets: tuple[AcquiredAsset, ...]
    documents: tuple[NormalizedDocument, ...]
    suppressed: bool = False

    def __post_init__(self) -> None:
        asset_ids = {item.id for item in self.assets}
        if any(item.source_record_id != self.metadata.id for item in self.assets):
            raise EconomicEvidenceGraphError("search corpus asset lineage is invalid")
        if any(item.acquired_asset_id not in asset_ids for item in self.documents):
            raise EconomicEvidenceGraphError("search corpus document lineage is invalid")


@dataclass(frozen=True, slots=True)
class AvailabilityDecision:
    """Versioned evidence for the earliest conservative retrieval date."""

    eligible_from: datetime
    basis: str
    evidence: str
    uncertainty: str | None

    def __post_init__(self) -> None:
        _require_utc(self.eligible_from, "availability eligible_from")
        if not self.basis.strip() or not self.evidence.strip():
            raise EconomicEvidenceGraphError("availability basis and evidence must not be blank")


@dataclass(frozen=True, slots=True)
class SearchWorkDocument:
    """Version-level work projection stored in the work search index."""

    id: str
    work_id: str
    document_version_id: str
    source_record_id: str
    source_code: str
    source_identifier: str
    source_url: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    identifiers: tuple[str, ...]
    published_at: date | None
    observed_at: datetime
    eligible_from: datetime
    date_basis: str
    date_evidence: str
    date_uncertainty: str | None
    temporal_safety_class: TemporalSafetyClass
    policy_version: int
    normalized_document_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]
    rights_statuses: tuple[str, ...]
    quality_warnings: tuple[str, ...]
    version_count: int
    embedding: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_search_document(
            self.id,
            self.work_id,
            self.document_version_id,
            self.source_record_id,
            self.title,
            self.source_url,
            self.observed_at,
            self.eligible_from,
            self.policy_version,
            self.embedding,
        )
        if self.version_count < 1:
            raise EconomicEvidenceGraphError("search work version count must be positive")


@dataclass(frozen=True, slots=True)
class SearchEvidenceDocument:
    """Provenance-complete evidence-unit projection stored in the evidence index."""

    id: str
    work_id: str
    document_version_id: str
    source_record_id: str
    normalized_document_id: str
    acquired_asset_id: str
    source_code: str
    source_identifier: str
    source_url: str
    content_url: str
    title: str
    authors: tuple[str, ...]
    published_at: date | None
    observed_at: datetime
    eligible_from: datetime
    date_basis: str
    date_evidence: str
    date_uncertainty: str | None
    temporal_safety_class: TemporalSafetyClass
    policy_version: int
    text: str
    section_path: tuple[str, ...]
    locator: str
    page_start: int | None
    page_end: int | None
    parser_name: str
    parser_version: str
    extraction_schema_version: str
    text_sha256: str
    content_sha256: str
    retrieved_at: datetime
    rights_status: str
    quality_warnings: tuple[str, ...]
    version_count: int
    embedding: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_search_document(
            self.id,
            self.work_id,
            self.document_version_id,
            self.source_record_id,
            self.title,
            self.source_url,
            self.observed_at,
            self.eligible_from,
            self.policy_version,
            self.embedding,
        )
        if not self.content_url.strip():
            raise EconomicEvidenceGraphError("search evidence content URL is required")
        _require_utc(self.retrieved_at, "search evidence retrieved_at")
        if not self.text.strip() or not self.locator.strip() or not self.parser_name.strip():
            raise EconomicEvidenceGraphError("search evidence text, locator, and parser are required")
        if not _SHA256_PATTERN.fullmatch(self.text_sha256) or not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise EconomicEvidenceGraphError("search evidence hashes must be SHA-256")
        if self.page_start is not None and (self.page_end is None or self.page_end < self.page_start):
            raise EconomicEvidenceGraphError("search evidence page interval is invalid")


@dataclass(frozen=True, slots=True)
class IndexGeneration:
    """Immutable, activated OpenSearch generation and corpus manifest."""

    id: str
    mapping_version: str
    retrieval_config_id: str
    embedding_config_id: str
    corpus_snapshot_id: str
    manifest_sha256: str
    work_index_name: str
    evidence_index_name: str
    work_document_count: int
    evidence_document_count: int
    created_at: datetime
    activated_at: datetime

    def __post_init__(self) -> None:
        if self.work_document_count < 0 or self.evidence_document_count < 0:
            raise EconomicEvidenceGraphError("index generation counts must not be negative")
        if not _SHA256_PATTERN.fullmatch(self.manifest_sha256):
            raise EconomicEvidenceGraphError("index generation manifest must be SHA-256")
        _require_utc(self.created_at, "index generation created_at")
        _require_utc(self.activated_at, "index generation activated_at")


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Typed source and publication filters."""

    source_codes: tuple[str, ...] = ()
    published_from: date | None = None
    published_to: date | None = None

    def __post_init__(self) -> None:
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_to < self.published_from
        ):
            raise EconomicEvidenceGraphError("search publication date range is invalid")


@dataclass(frozen=True, slots=True)
class HistoricalCutoff:
    """Strict as-of date applied before candidate generation."""

    at: datetime
    mode: HistoricalMode = HistoricalMode.STRICT

    def __post_init__(self) -> None:
        _require_utc(self.at, "historical cutoff")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Validated application-level literature search request."""

    query: str
    modes: tuple[SearchMode, ...]
    filters: SearchFilters
    historical_cutoff: HistoricalCutoff | None
    collapse_versions: bool
    limit: int
    cursor: str | None = None
    save_run: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip() or len(self.query) > 500:
            raise EconomicEvidenceGraphError("search query must contain 1 to 500 characters")
        if not self.modes or len(set(self.modes)) != len(self.modes):
            raise EconomicEvidenceGraphError("search modes must be non-empty and unique")
        if not 1 <= self.limit <= 50:
            raise EconomicEvidenceGraphError("search limit must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class ChannelContribution:
    """Recorded rank and reciprocal-rank contribution from one channel."""

    channel: str
    rank: int
    raw_score: float
    contribution: float

    def __post_init__(self) -> None:
        if self.rank < 1 or self.contribution < 0:
            raise EconomicEvidenceGraphError("search channel rank or contribution is invalid")


@dataclass(frozen=True, slots=True)
class TemporalResult:
    """Eligibility evidence echoed on every search result."""

    cutoff: datetime | None
    eligible: bool
    eligible_from: datetime
    date_basis: str
    date_evidence: str
    uncertainty: str | None
    safety_class: TemporalSafetyClass

    def __post_init__(self) -> None:
        if self.cutoff is not None:
            _require_utc(self.cutoff, "temporal result cutoff")
        _require_utc(self.eligible_from, "temporal result eligible_from")


@dataclass(frozen=True, slots=True)
class EvidenceSnippet:
    """Policy-limited evidence excerpt and exact lineage."""

    evidence_unit_id: str
    normalized_document_id: str
    acquired_asset_id: str
    text: str | None
    access_mode: str
    section_path: tuple[str, ...]
    locator: str
    page_start: int | None
    page_end: int | None
    content_sha256: str
    text_sha256: str
    retrieved_at: datetime
    parser_name: str
    parser_version: str
    extraction_schema_version: str
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_utc(self.retrieved_at, "evidence snippet retrieved_at")
        if self.text is not None and len(self.text) > 600:
            raise EconomicEvidenceGraphError("evidence snippets must be bounded")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One version-collapsed, explained literature-search result."""

    rank: int
    score: float
    work_id: str
    document_version_id: str
    source_record_id: str
    title: str
    authors: tuple[str, ...]
    published_at: date | None
    source_code: str
    source_identifier: str
    source_url: str
    policy_version: int
    alternative_version_count: int
    version_selection_reason: str
    contributions: tuple[ChannelContribution, ...]
    matched_phrases: tuple[str, ...]
    evidence: EvidenceSnippet | None
    temporal: TemporalResult
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.rank < 1 or self.score < 0 or self.alternative_version_count < 0:
            raise EconomicEvidenceGraphError("search result rank, score, or version count is invalid")


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Reproducible search response after provenance and policy gates."""

    query: str
    parsed_terms: tuple[str, ...]
    results: tuple[SearchResult, ...]
    index_generation_id: str
    corpus_snapshot_id: str
    retrieval_config_id: str
    modes_used: tuple[str, ...]
    omitted_channels: tuple[str, ...]
    warnings: tuple[str, ...]
    next_cursor: str | None
    search_run_id: str | None


@dataclass(frozen=True, slots=True)
class SearchRun:
    """Immutable request and result manifest for replay."""

    id: str
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    index_generation_id: str
    query_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.fullmatch(self.query_sha256):
            raise EconomicEvidenceGraphError("search run query hash must be SHA-256")
        _require_utc(self.created_at, "search run created_at")


def _validate_search_document(
    identifier: str,
    work_id: str,
    version_id: str,
    source_record_id: str,
    title: str,
    source_url: str,
    observed_at: datetime,
    eligible_from: datetime,
    policy_version: int,
    embedding: tuple[float, ...],
) -> None:
    """Validate fields shared by work and evidence projections."""
    if any(not value.strip() for value in (identifier, work_id, version_id, source_record_id, title, source_url)):
        raise EconomicEvidenceGraphError("search document identity and title fields are required")
    _require_utc(observed_at, "search document observed_at")
    _require_utc(eligible_from, "search document eligible_from")
    if policy_version < 1 or not embedding:
        raise EconomicEvidenceGraphError("search document policy version and embedding are required")


def _require_utc(value: datetime, field_name: str) -> None:
    """Require an aware UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be timezone-aware UTC")
