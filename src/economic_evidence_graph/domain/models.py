"""Immutable domain aggregates for the platform foundation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from economic_evidence_graph.exceptions import EconomicEvidenceGraphError, InvalidStateTransitionError

ScalarValue: TypeAlias = str | int | float | bool | None

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class TemporalSafetyClass(StrEnum):
    """Whether an artifact is safe for strict historical retrieval."""

    SOURCE_FACT = "T0"
    DETERMINISTIC = "T1"
    LEARNED = "T2"
    LATER_ANNOTATION = "T3"


class AccessStatus(StrEnum):
    """Acquisition and delivery state for a source manifestation."""

    STORED_OPEN = "stored_open"
    LINKED_OPEN = "linked_open"
    RESTRICTED = "restricted"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"
    RIGHTS_UNKNOWN = "rights_unknown"


class WorkType(StrEnum):
    """Initial scholarly and policy work categories."""

    WORKING_PAPER = "working_paper"
    JOURNAL_ARTICLE = "journal_article"
    POLICY_DOCUMENT = "policy_document"
    SPEECH = "speech"
    OTHER = "other"


class EvidenceState(StrEnum):
    """Review state for an evidence-field revision."""

    OBSERVED = "observed"
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"
    SUPERSEDED = "superseded"


class JobState(StrEnum):
    """Lifecycle state for a durable background job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _require_text(value: str, field_name: str) -> None:
    """Require a non-blank text value."""
    if not value.strip():
        raise EconomicEvidenceGraphError(f"{field_name} must not be blank")


def _require_code(value: str, field_name: str) -> None:
    """Require a stable lowercase machine code."""
    if not _CODE_PATTERN.fullmatch(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be a lowercase machine code")


def _require_utc(value: datetime, field_name: str) -> None:
    """Require an aware UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be timezone-aware UTC")


def _require_sha256(value: str, field_name: str) -> None:
    """Require a lowercase SHA-256 digest."""
    if not _SHA256_PATTERN.fullmatch(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class Source:
    """A registered upstream corpus source."""

    id: str
    code: str
    display_name: str
    source_kind: str

    def __post_init__(self) -> None:
        _require_text(self.id, "source id")
        _require_code(self.code, "source code")
        _require_text(self.display_name, "source display name")
        _require_code(self.source_kind, "source kind")


@dataclass(frozen=True, slots=True)
class SourcePolicyVersion:
    """The exact source-policy decision used for acquisition."""

    id: str
    source_id: str
    version: int
    effective_at: datetime
    review_status: str

    def __post_init__(self) -> None:
        if self.version < 1:
            raise EconomicEvidenceGraphError("source policy version must be positive")
        _require_utc(self.effective_at, "source policy effective_at")
        _require_code(self.review_status, "source policy review status")


@dataclass(frozen=True, slots=True)
class RetrievalEvent:
    """Immutable observation of one source retrieval."""

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

    def __post_init__(self) -> None:
        _require_utc(self.retrieved_at, "retrieval retrieved_at")
        if not 100 <= self.http_status <= 599:
            raise EconomicEvidenceGraphError("retrieval HTTP status must be between 100 and 599")
        _require_sha256(self.content_sha256, "retrieval content_sha256")
        _require_text(self.requested_url, "retrieval requested URL")
        _require_text(self.resolved_url, "retrieval resolved URL")
        _require_text(self.media_type, "retrieval media type")
        _require_text(self.collector_version, "retrieval collector version")


@dataclass(frozen=True, slots=True)
class RawAsset:
    """A content-addressed source asset or index-only observation."""

    id: str
    retrieval_event_id: str
    sha256: str
    media_type: str
    byte_length: int
    storage_uri: str | None
    access_status: AccessStatus

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "raw asset sha256")
        if self.byte_length < 0:
            raise EconomicEvidenceGraphError("raw asset byte length must not be negative")
        if self.access_status is AccessStatus.STORED_OPEN and self.storage_uri is None:
            raise EconomicEvidenceGraphError("stored assets require a storage URI")


@dataclass(frozen=True, slots=True)
class ExternalIdentifier:
    """A source-supplied persistent or local identifier."""

    id: str
    scheme: str
    value: str

    def __post_init__(self) -> None:
        _require_code(self.scheme, "external identifier scheme")
        _require_text(self.value, "external identifier value")


@dataclass(frozen=True, slots=True)
class Work:
    """A stable intellectual work independent of any one document version."""

    id: str
    title: str
    work_type: WorkType
    authors: tuple[str, ...]
    identifiers: tuple[ExternalIdentifier, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.title, "work title")
        if not self.authors or any(not author.strip() for author in self.authors):
            raise EconomicEvidenceGraphError("work authors must contain non-blank names")


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """A dated content state belonging to a canonical work."""

    id: str
    work_id: str
    raw_asset_id: str
    version_label: str
    asserted_at: date | None
    first_observed_at: datetime
    eligible_from: datetime | None
    temporal_safety_class: TemporalSafetyClass

    def __post_init__(self) -> None:
        _require_text(self.version_label, "document version label")
        _require_utc(self.first_observed_at, "document version first_observed_at")
        if self.eligible_from is not None:
            _require_utc(self.eligible_from, "document version eligible_from")


@dataclass(frozen=True, slots=True)
class Manifestation:
    """One source-specific representation of a document version."""

    id: str
    document_version_id: str
    source_id: str
    source_identifier: str
    source_url: str
    access_status: AccessStatus
    license_uri: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.source_identifier, "manifestation source identifier")
        _require_text(self.source_url, "manifestation source URL")


@dataclass(frozen=True, slots=True)
class ExtractionArtifact:
    """A versioned deterministic extraction of a raw document."""

    id: str
    document_version_id: str
    raw_asset_id: str
    parser_name: str
    parser_version: str
    schema_version: str
    text_sha256: str
    created_at: datetime
    temporal_safety_class: TemporalSafetyClass

    def __post_init__(self) -> None:
        _require_code(self.parser_name, "parser name")
        _require_text(self.parser_version, "parser version")
        _require_text(self.schema_version, "extraction schema version")
        _require_sha256(self.text_sha256, "extraction text_sha256")
        _require_utc(self.created_at, "extraction created_at")


@dataclass(frozen=True, slots=True)
class EvidenceUnit:
    """A bounded passage with structural and page provenance."""

    id: str
    extraction_artifact_id: str
    ordinal: int
    section_path: tuple[str, ...]
    page_start: int
    page_end: int
    text: str
    temporal_safety_class: TemporalSafetyClass

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise EconomicEvidenceGraphError("evidence-unit ordinal must not be negative")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise EconomicEvidenceGraphError("evidence-unit page interval is invalid")
        if not self.section_path or any(not section.strip() for section in self.section_path):
            raise EconomicEvidenceGraphError("evidence-unit section path must be populated")
        _require_text(self.text, "evidence-unit text")


@dataclass(frozen=True, slots=True)
class EvidenceAnchor:
    """Exact support for a structured evidence value."""

    evidence_unit_id: str
    locator: str
    quote: str

    def __post_init__(self) -> None:
        _require_text(self.locator, "evidence anchor locator")
        _require_text(self.quote, "evidence anchor quote")


@dataclass(frozen=True, slots=True)
class EvidenceFieldRevision:
    """One immutable revision of a typed evidence field."""

    id: str
    field_name: str
    display_value: str
    normalized_value: ScalarValue
    units: str | None
    applicability: str
    state: EvidenceState
    lineage_method: str
    confidence: float
    anchors: tuple[EvidenceAnchor, ...]

    def __post_init__(self) -> None:
        _require_code(self.field_name, "evidence field name")
        _require_text(self.display_value, "evidence field display value")
        _require_text(self.applicability, "evidence field applicability")
        _require_code(self.lineage_method, "evidence field lineage method")
        if not 0.0 <= self.confidence <= 1.0:
            raise EconomicEvidenceGraphError("evidence field confidence must be between zero and one")
        if not self.anchors:
            raise EconomicEvidenceGraphError("evidence field revisions require at least one source anchor")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Economics-specific evidence linked to a work and exact source anchors."""

    id: str
    work_id: str
    document_version_id: str
    record_type: str
    schema_version: str
    fields: tuple[EvidenceFieldRevision, ...]

    def __post_init__(self) -> None:
        _require_code(self.record_type, "evidence record type")
        _require_text(self.schema_version, "evidence record schema version")
        if not self.fields:
            raise EconomicEvidenceGraphError("evidence records require at least one field")


@dataclass(frozen=True, slots=True)
class SnapshotMember:
    """The exact version and extraction selected for a work in a snapshot."""

    work_id: str
    document_version_id: str
    extraction_artifact_id: str


@dataclass(frozen=True, slots=True)
class CorpusSnapshot:
    """A sealed logical corpus manifest."""

    id: str
    schema_version: str
    created_at: datetime
    source_policy_version_ids: tuple[str, ...]
    members: tuple[SnapshotMember, ...]
    manifest_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "corpus snapshot schema version")
        _require_utc(self.created_at, "corpus snapshot created_at")
        _require_sha256(self.manifest_sha256, "corpus snapshot manifest_sha256")
        if not self.members:
            raise EconomicEvidenceGraphError("corpus snapshots require at least one member")


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """An atomic ingestion unit with complete acquisition and evidence lineage."""

    sources: tuple[Source, ...]
    source_policies: tuple[SourcePolicyVersion, ...]
    retrieval_events: tuple[RetrievalEvent, ...]
    raw_assets: tuple[RawAsset, ...]
    work: Work
    document_versions: tuple[DocumentVersion, ...]
    manifestations: tuple[Manifestation, ...]
    extraction_artifacts: tuple[ExtractionArtifact, ...]
    evidence_units: tuple[EvidenceUnit, ...]
    evidence_records: tuple[EvidenceRecord, ...]
    snapshot: CorpusSnapshot

    def __post_init__(self) -> None:
        self.validate_lineage()

    def validate_lineage(self) -> None:
        """Validate every foreign reference in the bundle.

        Raises:
            EconomicEvidenceGraphError: If any lineage edge is unresolved or inconsistent.
        """
        source_ids = {source.id for source in self.sources}
        policy_ids = {policy.id for policy in self.source_policies}
        retrieval_ids = {event.id for event in self.retrieval_events}
        asset_ids = {asset.id for asset in self.raw_assets}
        version_ids = {version.id for version in self.document_versions}
        extraction_ids = {artifact.id for artifact in self.extraction_artifacts}
        extraction_version_ids = {artifact.id: artifact.document_version_id for artifact in self.extraction_artifacts}
        unit_version_ids = {
            unit.id: extraction_version_ids.get(unit.extraction_artifact_id) for unit in self.evidence_units
        }
        unit_ids = {unit.id for unit in self.evidence_units}

        checks = [
            all(policy.source_id in source_ids for policy in self.source_policies),
            all(
                event.source_id in source_ids and event.source_policy_version_id in policy_ids
                for event in self.retrieval_events
            ),
            all(asset.retrieval_event_id in retrieval_ids for asset in self.raw_assets),
            all(
                version.work_id == self.work.id and version.raw_asset_id in asset_ids
                for version in self.document_versions
            ),
            all(
                manifestation.document_version_id in version_ids and manifestation.source_id in source_ids
                for manifestation in self.manifestations
            ),
            all(
                artifact.document_version_id in version_ids and artifact.raw_asset_id in asset_ids
                for artifact in self.extraction_artifacts
            ),
            all(unit.extraction_artifact_id in extraction_ids for unit in self.evidence_units),
            all(
                record.work_id == self.work.id
                and record.document_version_id in version_ids
                and all(
                    anchor.evidence_unit_id in unit_ids
                    and unit_version_ids.get(anchor.evidence_unit_id) == record.document_version_id
                    for item in record.fields
                    for anchor in item.anchors
                )
                for record in self.evidence_records
            ),
            all(
                member.work_id == self.work.id
                and member.document_version_id in version_ids
                and member.extraction_artifact_id in extraction_ids
                and extraction_version_ids.get(member.extraction_artifact_id) == member.document_version_id
                for member in self.snapshot.members
            ),
            any(
                member.work_id == self.work.id
                and member.document_version_id in version_ids
                and member.extraction_artifact_id in extraction_ids
                for member in self.snapshot.members
            ),
            all(policy_id in policy_ids for policy_id in self.snapshot.source_policy_version_ids),
        ]
        if not all(checks):
            raise EconomicEvidenceGraphError("evidence bundle contains an unresolved lineage reference")


@dataclass(frozen=True, slots=True)
class Job:
    """A durable, idempotent unit of background work."""

    id: str
    kind: str
    state: JobState
    idempotency_key: str
    input: MappingProxyType[str, ScalarValue]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: MappingProxyType[str, ScalarValue] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.kind, "job kind")
        _require_text(self.idempotency_key, "job idempotency key")
        _require_utc(self.created_at, "job created_at")
        if self.started_at is not None:
            _require_utc(self.started_at, "job started_at")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "job finished_at")

    @classmethod
    def pending(
        cls,
        *,
        id: str,
        kind: str,
        idempotency_key: str,
        input: dict[str, ScalarValue],
        created_at: datetime,
    ) -> Job:
        """Create a pending job with immutable input."""
        return cls(
            id=id,
            kind=kind,
            state=JobState.PENDING,
            idempotency_key=idempotency_key,
            input=MappingProxyType(dict(input)),
            created_at=created_at,
        )

    def start(self, at: datetime) -> Job:
        """Move a pending job into the running state."""
        if self.state is not JobState.PENDING:
            raise InvalidStateTransitionError("only pending jobs can start")
        return replace(self, state=JobState.RUNNING, started_at=at)

    def succeed(self, at: datetime, output: dict[str, ScalarValue]) -> Job:
        """Complete a running job successfully."""
        if self.state is not JobState.RUNNING:
            raise InvalidStateTransitionError("only running jobs can succeed")
        return replace(
            self,
            state=JobState.SUCCEEDED,
            finished_at=at,
            output=MappingProxyType(dict(output)),
        )

    def fail(self, at: datetime, *, code: str, message: str) -> Job:
        """Complete a running job with a structured failure."""
        if self.state is not JobState.RUNNING:
            raise InvalidStateTransitionError("only running jobs can fail")
        return replace(
            self,
            state=JobState.FAILED,
            finished_at=at,
            error_code=code,
            error_message=message,
        )
