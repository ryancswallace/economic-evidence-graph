"""Governed source connector contracts."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from economic_evidence_graph.domain import ContentLink, IdentifierValue, RequestTrace


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    """Exact source metadata sufficient to plan governed retrieval."""

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
    raw_metadata: bytes
    metadata_media_type: str
    discovered_at: datetime


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    """A bounded, resumable page of source discovery results."""

    records: tuple[DiscoveryRecord, ...]
    next_cursor: str | None
    source_checkpoint: str | None
    traces: tuple[RequestTrace, ...]


@dataclass(frozen=True, slots=True)
class AcquisitionCandidate:
    """Retrieved source representation before normalization and resolution."""

    record: DiscoveryRecord
    requested_url: str
    resolved_url: str
    media_type: str
    content: bytes
    retrieved_at: datetime
    http_status: int
    license_uri: str | None
    trace: RequestTrace


@dataclass(frozen=True, slots=True)
class AcquisitionFailureCandidate:
    """Safe, auditable failure for one requested representation."""

    record: DiscoveryRecord
    requested_url: str
    failure_code: str
    detail: str
    occurred_at: datetime
    trace: RequestTrace


@dataclass(frozen=True, slots=True)
class AcquisitionBatch:
    """All successes and failures from one record's bounded retrieval plan."""

    candidates: tuple[AcquisitionCandidate, ...]
    failures: tuple[AcquisitionFailureCandidate, ...]


class SourceConnector(Protocol):
    """Contract implemented by RePEc, Crossref, CORE, and Fed connectors."""

    @property
    def source_code(self) -> str:
        """Return the stable source registry code."""
        ...

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Return bounded source records and their governance trace."""
        ...

    def acquire(self, record: DiscoveryRecord) -> AcquisitionBatch:
        """Retrieve permitted representations and retain bounded failure evidence."""
        ...
