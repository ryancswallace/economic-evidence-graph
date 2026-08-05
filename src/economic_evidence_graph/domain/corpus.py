"""Immutable records for governed corpus acquisition and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PolicyOutcome(StrEnum):
    """Result of evaluating one source action."""

    ALLOW = "allow"
    DENY = "deny"


class ChangeKind(StrEnum):
    """Observed source-record change relative to its prior observation."""

    NEW = "new"
    UNCHANGED = "unchanged"
    REVISED = "revised"
    DELETED = "deleted"
    CORRECTED = "corrected"
    EMBARGOED = "embargoed"
    IDENTIFIER_CHANGED = "identifier_changed"


class QuarantineReason(StrEnum):
    """Stable reason why an asset did not enter the normalized corpus."""

    ENCRYPTED = "encrypted"
    MALFORMED = "malformed"
    EMPTY_TEXT = "empty_text"
    OCR_REQUIRED = "ocr_required"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    SIZE_LIMIT = "size_limit"
    POLICY_DENIED = "policy_denied"
    SECURITY_REJECTED = "security_rejected"


@dataclass(frozen=True, slots=True)
class IdentifierValue:
    """A normalized source-supplied identifier without a resolution decision."""

    scheme: str
    value: str

    def __post_init__(self) -> None:
        if not self.scheme.strip() or not self.value.strip():
            raise EconomicEvidenceGraphError("identifier scheme and value must not be blank")


@dataclass(frozen=True, slots=True)
class ContentLink:
    """A source-declared candidate representation."""

    url: str
    media_type: str | None
    content_class: str
    license_uri: str | None = None

    def __post_init__(self) -> None:
        if not self.url.strip() or not self.content_class.strip():
            raise EconomicEvidenceGraphError("content links require a URL and content class")


@dataclass(frozen=True, slots=True)
class SourceMetadataRecord:
    """One exact, immutable source metadata observation."""

    id: str
    source_code: str
    source_identifier: str
    metadata_url: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    published_at: date | None
    asserted_updated_at: datetime | None
    identifiers: tuple[IdentifierValue, ...]
    content_links: tuple[ContentLink, ...]
    observed_at: datetime
    metadata_sha256: str
    metadata_storage_uri: str

    def __post_init__(self) -> None:
        required = (self.id, self.source_code, self.source_identifier, self.metadata_url, self.title)
        if any(not value.strip() for value in required):
            raise EconomicEvidenceGraphError("source metadata identity and title fields must not be blank")
        if any(not author.strip() for author in self.authors):
            raise EconomicEvidenceGraphError("source metadata authors must not contain blanks")
        _require_utc(self.observed_at, "source metadata observed_at")
        if self.asserted_updated_at is not None:
            _require_utc(self.asserted_updated_at, "source metadata asserted_updated_at")
        _require_sha256(self.metadata_sha256, "source metadata metadata_sha256")
        if not self.metadata_storage_uri.strip():
            raise EconomicEvidenceGraphError("source metadata storage URI must not be blank")


@dataclass(frozen=True, slots=True)
class PolicyDecisionRecord:
    """Auditable result of one deterministic source-policy evaluation."""

    id: str
    source_code: str
    policy_version: int
    action: str
    url: str
    content_class: str
    outcome: PolicyOutcome
    reason: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.policy_version < 1:
            raise EconomicEvidenceGraphError("policy decision version must be positive")
        required = (self.id, self.source_code, self.action, self.url, self.reason)
        if any(not value.strip() for value in required):
            raise EconomicEvidenceGraphError("policy decision fields must not be blank")
        _require_utc(self.decided_at, "policy decision decided_at")


@dataclass(frozen=True, slots=True)
class RobotsObservation:
    """Exact observation used to decide whether crawler access was permitted."""

    id: str
    host: str
    robots_url: str
    target_url: str
    observed_at: datetime
    http_status: int
    content_sha256: str
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not 100 <= self.http_status <= 599:
            raise EconomicEvidenceGraphError("robots HTTP status must be between 100 and 599")
        _require_utc(self.observed_at, "robots observed_at")
        _require_sha256(self.content_sha256, "robots content_sha256")


@dataclass(frozen=True, slots=True)
class RequestTrace:
    """Complete policy and robots trace for one HTTP request chain."""

    decisions: tuple[PolicyDecisionRecord, ...]
    robots: tuple[RobotsObservation, ...]


@dataclass(frozen=True, slots=True)
class AcquiredAsset:
    """A governed source representation stored by content address."""

    id: str
    source_record_id: str
    requested_url: str
    resolved_url: str
    retrieved_at: datetime
    http_status: int
    media_type: str
    sha256: str
    byte_length: int
    storage_uri: str
    rights_status: str
    license_uri: str | None

    def __post_init__(self) -> None:
        _require_utc(self.retrieved_at, "acquired asset retrieved_at")
        _require_sha256(self.sha256, "acquired asset sha256")
        if self.byte_length < 0:
            raise EconomicEvidenceGraphError("acquired asset byte length must not be negative")


@dataclass(frozen=True, slots=True)
class NormalizedUnit:
    """Structurally anchored deterministic text unit."""

    id: str
    document_id: str
    ordinal: int
    section_path: tuple[str, ...]
    locator: str
    page_start: int | None
    page_end: int | None
    text: str

    def __post_init__(self) -> None:
        if self.ordinal < 0 or not self.locator.strip() or not self.text.strip():
            raise EconomicEvidenceGraphError("normalized unit ordinal, locator, and text are invalid")
        if (self.page_start is None) != (self.page_end is None):
            raise EconomicEvidenceGraphError("normalized unit page bounds must both be present or absent")
        if self.page_start is not None and (
            self.page_start < 1 or self.page_end is None or self.page_end < self.page_start
        ):
            raise EconomicEvidenceGraphError("normalized unit page bounds are invalid")


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Deterministic parser quality signals and routing warnings."""

    parser_route: str
    page_count: int | None
    character_count: int
    text_density: float | None
    ocr_required: bool
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.character_count < 0 or (self.page_count is not None and self.page_count < 1):
            raise EconomicEvidenceGraphError("quality report counts are invalid")


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """Versioned normalized text plus stable structural units."""

    id: str
    acquired_asset_id: str
    parser_name: str
    parser_version: str
    schema_version: str
    text_sha256: str
    text_storage_uri: str
    created_at: datetime
    units: tuple[NormalizedUnit, ...]
    quality: QualityReport

    def __post_init__(self) -> None:
        _require_sha256(self.text_sha256, "normalized document text_sha256")
        _require_utc(self.created_at, "normalized document created_at")
        if not self.units:
            raise EconomicEvidenceGraphError("normalized documents require at least one unit")


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """Reviewable terminal route for an unsafe or unparseable asset."""

    id: str
    acquired_asset_id: str
    reason: QuarantineReason
    detail: str
    created_at: datetime
    retryable: bool

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise EconomicEvidenceGraphError("quarantine detail must not be blank")
        _require_utc(self.created_at, "quarantine created_at")


@dataclass(frozen=True, slots=True)
class AcquisitionFailureRecord:
    """Immutable evidence that one representation could not be acquired."""

    id: str
    source_record_id: str
    requested_url: str
    failure_code: str
    detail: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.source_record_id.strip() or not self.requested_url.startswith(("http://", "https://")):
            raise EconomicEvidenceGraphError("acquisition failure lineage or URL is invalid")
        if not self.failure_code.strip() or not self.detail.strip():
            raise EconomicEvidenceGraphError("acquisition failure code and detail must not be blank")
        _require_utc(self.occurred_at, "acquisition failure occurred_at")


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    """Conservative, reviewable proposal to associate two source records."""

    id: str
    left_source_record_id: str
    right_source_record_id: str
    method: str
    confidence: float
    features: tuple[str, ...]
    hard_conflict: bool
    created_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0 or not self.method.strip():
            raise EconomicEvidenceGraphError("identity candidate method or confidence is invalid")
        _require_utc(self.created_at, "identity candidate created_at")


@dataclass(frozen=True, slots=True)
class SourceChangeEvent:
    """Immutable comparison between source metadata observations."""

    id: str
    source_code: str
    source_identifier: str
    previous_record_id: str | None
    current_record_id: str
    kind: ChangeKind
    detected_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.detected_at, "source change detected_at")


@dataclass(frozen=True, slots=True)
class SuppressionRecord:
    """Immutable tombstone that blocks future acquisition and delivery."""

    id: str
    scope_type: str
    scope_value: str
    reason: str
    created_at: datetime
    physical_deletion_requested: bool

    def __post_init__(self) -> None:
        if self.scope_type not in {"source_record", "url", "asset"}:
            raise EconomicEvidenceGraphError("suppression scope type is invalid")
        if not self.scope_value.strip() or not self.reason.strip():
            raise EconomicEvidenceGraphError("suppression value and reason must not be blank")
        _require_utc(self.created_at, "suppression created_at")


@dataclass(frozen=True, slots=True)
class IngestedRecord:
    """Atomic Step 2 persistence unit for one source record."""

    metadata: SourceMetadataRecord
    assets: tuple[AcquiredAsset, ...]
    documents: tuple[NormalizedDocument, ...]
    quarantines: tuple[QuarantineRecord, ...]
    acquisition_failures: tuple[AcquisitionFailureRecord, ...]
    identity_candidates: tuple[IdentityCandidate, ...]
    change: SourceChangeEvent
    decisions: tuple[PolicyDecisionRecord, ...]
    robots: tuple[RobotsObservation, ...]


def _require_utc(value: datetime, field_name: str) -> None:
    """Require an aware UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be timezone-aware UTC")


def _require_sha256(value: str, field_name: str) -> None:
    """Require a lowercase SHA-256 digest."""
    if not _SHA256_PATTERN.fullmatch(value):
        raise EconomicEvidenceGraphError(f"{field_name} must be a lowercase SHA-256 digest")
