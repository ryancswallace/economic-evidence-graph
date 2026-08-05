"""Canonical catalog and job persistence protocols."""

from typing import Protocol

from economic_evidence_graph.domain import (
    EvidenceBundle,
    IdentifierValue,
    IngestedRecord,
    Job,
    PolicyDecisionRecord,
    RobotsObservation,
    SourceMetadataRecord,
    SuppressionRecord,
)


class CatalogRepository(Protocol):
    """Persistence boundary for immutable evidence bundles."""

    def put_bundle(self, bundle: EvidenceBundle) -> None:
        """Persist a bundle atomically and idempotently."""
        ...

    def get_bundle(self, work_id: str) -> EvidenceBundle:
        """Return one work and all lineage needed to interpret it."""
        ...

    def check(self) -> None:
        """Raise when the repository is unavailable."""
        ...

    def close(self) -> None:
        """Release provider-owned connection pools."""
        ...


class JobRepository(Protocol):
    """Persistence boundary for durable idempotent jobs."""

    def add(self, item: Job) -> None:
        """Create a job unless its identifier or idempotency key conflicts."""
        ...

    def save(self, item: Job) -> None:
        """Persist a valid later revision of an existing job."""
        ...

    def get(self, job_id: str) -> Job:
        """Return a job by identifier."""
        ...

    def find_by_idempotency_key(self, key: str) -> Job | None:
        """Return the prior job for a client operation, if any."""
        ...

    def close(self) -> None:
        """Release provider-owned connection pools."""
        ...


class CorpusRepository(Protocol):
    """Persistence boundary for idempotent Step 2 ingestion observations."""

    def put_ingested_record(self, item: IngestedRecord) -> None:
        """Persist one complete source-record result atomically and idempotently."""
        ...

    def put_governance_trace(
        self,
        decisions: tuple[PolicyDecisionRecord, ...],
        robots: tuple[RobotsObservation, ...],
    ) -> None:
        """Persist discovery policy and robots observations idempotently."""
        ...

    def latest_source_record(self, source_code: str, source_identifier: str) -> SourceMetadataRecord | None:
        """Return the latest observed metadata for change detection."""
        ...

    def identity_matches(
        self,
        identifiers: tuple[IdentifierValue, ...],
        *,
        exclude_record_id: str,
    ) -> tuple[SourceMetadataRecord, ...]:
        """Return records sharing an exact approved identifier."""
        ...

    def put_suppression(self, item: SuppressionRecord) -> None:
        """Persist an immutable suppression tombstone."""
        ...

    def is_suppressed(self, scope_type: str, scope_value: str) -> bool:
        """Return whether an active tombstone blocks the supplied scope."""
        ...

    def corpus_usage(self) -> tuple[int, int]:
        """Return persisted source-record count and raw acquired bytes."""
        ...

    def close(self) -> None:
        """Release provider-owned resources."""
        ...
