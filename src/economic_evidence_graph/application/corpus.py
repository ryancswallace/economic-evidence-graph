"""Idempotent governed corpus ingestion orchestration."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

from economic_evidence_graph.domain import (
    AcquiredAsset,
    AcquisitionFailureRecord,
    ChangeKind,
    IdentityCandidate,
    IngestedRecord,
    NormalizedDocument,
    NormalizedUnit,
    PolicyDecisionRecord,
    PolicyOutcome,
    QuarantineRecord,
    RobotsObservation,
    SourceChangeEvent,
    SourceMetadataRecord,
    SuppressionRecord,
    stable_id,
)
from economic_evidence_graph.exceptions import CapacityExceededError, EconomicEvidenceGraphError, PolicyDeniedError
from economic_evidence_graph.parsers import DocumentParserRouter, ParseFailure
from economic_evidence_graph.policy import PolicyEngine
from economic_evidence_graph.ports import (
    ArtifactStore,
    Clock,
    CorpusRepository,
    DiscoveryRecord,
    IdFactory,
    SourceConnector,
)


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    """Bounded operator-facing result of one connector page."""

    source_code: str
    discovered: int
    acquired_assets: int
    normalized_documents: int
    quarantined_assets: int
    failed_records: int
    next_cursor: str | None
    failure_codes: tuple[tuple[str, int], ...] = ()
    acquisition_failures: int = 0
    acquisition_failure_codes: tuple[tuple[str, int], ...] = ()


class CorpusIngestionService:
    """Run discovery through durable metadata, assets, parsing, and candidates."""

    def __init__(
        self,
        *,
        repository: CorpusRepository,
        artifacts: ArtifactStore,
        parsers: DocumentParserRouter,
        policy: PolicyEngine,
        clock: Clock,
        ids: IdFactory,
        maximum_records: int = 1_000,
        maximum_source_bytes: int = 10_737_418_240,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._parsers = parsers
        self._policy = policy
        self._clock = clock
        self._ids = ids
        if maximum_records < 1 or maximum_source_bytes < 1:
            raise ValueError("corpus pilot ceilings must be positive")
        self._maximum_records = maximum_records
        self._maximum_source_bytes = maximum_source_bytes

    def ingest_page(self, connector: SourceConnector, *, cursor: str | None, limit: int) -> IngestionSummary:
        """Ingest one bounded connector page with per-record poison isolation."""
        record_usage, byte_usage = self._repository.corpus_usage()
        remaining_records = self._maximum_records - record_usage
        if remaining_records <= 0 or byte_usage >= self._maximum_source_bytes:
            raise CapacityExceededError("the local corpus pilot ceiling has been reached")
        page = connector.discover(cursor=cursor, limit=min(limit, remaining_records))
        page_decisions = tuple(decision for trace in page.traces for decision in trace.decisions)
        page_robots = tuple(observation for trace in page.traces for observation in trace.robots)
        self._repository.put_governance_trace(page_decisions, page_robots)
        asset_count = document_count = quarantine_count = failures = 0
        failure_codes: Counter[str] = Counter()
        acquisition_failure_codes: Counter[str] = Counter()

        for discovered in page.records:
            try:
                result = self._ingest_record(
                    connector,
                    discovered,
                    page_decisions,
                    page_robots,
                    remaining_source_bytes=self._maximum_source_bytes - byte_usage,
                )
            except EconomicEvidenceGraphError as error:
                failures += 1
                failure_codes[error.code] += 1
                continue
            self._repository.put_ingested_record(result)
            asset_count += len(result.assets)
            byte_usage += sum(asset.byte_length for asset in result.assets)
            document_count += len(result.documents)
            quarantine_count += len(result.quarantines)
            for failure in result.acquisition_failures:
                acquisition_failure_codes[failure.failure_code] += 1

        return IngestionSummary(
            source_code=connector.source_code,
            discovered=len(page.records),
            acquired_assets=asset_count,
            normalized_documents=document_count,
            quarantined_assets=quarantine_count,
            failed_records=failures,
            next_cursor=page.next_cursor,
            failure_codes=tuple(sorted(failure_codes.items())),
            acquisition_failures=sum(acquisition_failure_codes.values()),
            acquisition_failure_codes=tuple(sorted(acquisition_failure_codes.items())),
        )

    def _ingest_record(
        self,
        connector: SourceConnector,
        discovered: DiscoveryRecord,
        page_decisions: tuple[PolicyDecisionRecord, ...],
        page_robots: tuple[RobotsObservation, ...],
        *,
        remaining_source_bytes: int,
    ) -> IngestedRecord:
        """Build one complete immutable ingestion result."""
        metadata_ref = self._artifacts.put_bytes(discovered.raw_metadata, media_type=discovered.metadata_media_type)
        metadata = SourceMetadataRecord(
            id=stable_id(
                "met",
                discovered.source_code,
                discovered.source_identifier,
                metadata_ref.sha256,
                discovered.discovered_at.isoformat(),
            ),
            source_code=discovered.source_code,
            source_identifier=discovered.source_identifier,
            metadata_url=discovered.metadata_url,
            title=discovered.title,
            authors=discovered.authors,
            abstract=discovered.abstract,
            published_at=discovered.published_at,
            asserted_updated_at=discovered.asserted_updated_at,
            identifiers=discovered.identifiers,
            content_links=discovered.content_links,
            observed_at=discovered.discovered_at,
            metadata_sha256=metadata_ref.sha256,
            metadata_storage_uri=metadata_ref.uri,
        )
        decisions = list(page_decisions)
        discovery_url = next(
            (
                decision.url
                for decision in reversed(page_decisions)
                if decision.source_code == metadata.source_code
                and decision.action == "discover_metadata"
                and decision.outcome is PolicyOutcome.ALLOW
            ),
            metadata.metadata_url,
        )
        decisions.append(
            self._authorize(metadata.source_code, "store_metadata", discovery_url, "bibliographic_metadata")
        )
        prior = self._repository.latest_source_record(metadata.source_code, metadata.source_identifier)
        comparison = None if prior is not None and prior.id == metadata.id else prior
        change = SourceChangeEvent(
            id=stable_id("chg", metadata.id, "none" if comparison is None else comparison.id),
            source_code=metadata.source_code,
            source_identifier=metadata.source_identifier,
            previous_record_id=None if comparison is None else comparison.id,
            current_record_id=metadata.id,
            kind=(
                ChangeKind.NEW
                if comparison is None
                else ChangeKind.UNCHANGED
                if comparison.metadata_sha256 == metadata.metadata_sha256
                else ChangeKind.REVISED
            ),
            detected_at=metadata.observed_at,
        )

        assets: list[AcquiredAsset] = []
        documents: list[NormalizedDocument] = []
        quarantines: list[QuarantineRecord] = []
        robots = list(page_robots)
        suppression_key = f"{metadata.source_code}:{metadata.source_identifier}"
        if self._repository.is_suppressed("source_record", suppression_key):
            return IngestedRecord(
                metadata=metadata,
                assets=(),
                documents=(),
                quarantines=(),
                acquisition_failures=(),
                identity_candidates=(),
                change=change,
                decisions=tuple(decisions),
                robots=tuple(robots),
            )
        acquisition = connector.acquire(discovered)
        acquisition_failures = tuple(
            AcquisitionFailureRecord(
                id=stable_id("acf", metadata.id, failure.requested_url, failure.failure_code),
                source_record_id=metadata.id,
                requested_url=failure.requested_url,
                failure_code=failure.failure_code,
                detail=failure.detail,
                occurred_at=failure.occurred_at,
            )
            for failure in acquisition.failures
        )
        for failure in acquisition.failures:
            decisions.extend(failure.trace.decisions)
            robots.extend(failure.trace.robots)
        for candidate in acquisition.candidates:
            decisions.extend(candidate.trace.decisions)
            robots.extend(candidate.trace.robots)
            if not 200 <= candidate.http_status < 300:
                continue
            if len(candidate.content) > remaining_source_bytes:
                raise CapacityExceededError("candidate would exceed the local source-byte ceiling")
            remaining_source_bytes -= len(candidate.content)
            decisions.append(
                self._authorize(metadata.source_code, "store_raw", candidate.resolved_url, "retrieved_content")
            )
            ref = self._artifacts.put_bytes(candidate.content, media_type=candidate.media_type)
            asset = AcquiredAsset(
                id=stable_id("ast", metadata.id, candidate.resolved_url, ref.sha256),
                source_record_id=metadata.id,
                requested_url=candidate.requested_url,
                resolved_url=candidate.resolved_url,
                retrieved_at=candidate.retrieved_at,
                http_status=candidate.http_status,
                media_type=candidate.media_type,
                sha256=ref.sha256,
                byte_length=ref.byte_length,
                storage_uri=ref.uri,
                rights_status=_rights_status(candidate.license_uri),
                license_uri=candidate.license_uri,
            )
            assets.append(asset)
            decisions.append(
                self._authorize(metadata.source_code, "extract_text", asset.resolved_url, "retrieved_content")
            )
            try:
                parsed = self._parsers.parse(candidate.content, media_type=candidate.media_type)
            except ParseFailure as error:
                quarantines.append(
                    QuarantineRecord(
                        id=stable_id("qua", asset.id, error.reason.value),
                        acquired_asset_id=asset.id,
                        reason=error.reason,
                        detail=str(error),
                        created_at=candidate.retrieved_at,
                        retryable=error.retryable,
                    )
                )
                continue
            text_ref = self._artifacts.put_bytes(parsed.text.encode(), media_type="text/plain")
            document_id = stable_id("nrm", asset.id, parsed.parser_name, parsed.parser_version, text_ref.sha256)
            units = tuple(
                NormalizedUnit(
                    id=stable_id("nru", document_id, str(unit.ordinal), hashlib.sha256(unit.text.encode()).hexdigest()),
                    document_id=document_id,
                    ordinal=unit.ordinal,
                    section_path=unit.section_path,
                    locator=unit.locator,
                    page_start=unit.page_start,
                    page_end=unit.page_end,
                    text=unit.text,
                )
                for unit in parsed.units
            )
            documents.append(
                NormalizedDocument(
                    id=document_id,
                    acquired_asset_id=asset.id,
                    parser_name=parsed.parser_name,
                    parser_version=parsed.parser_version,
                    schema_version=parsed.schema_version,
                    text_sha256=text_ref.sha256,
                    text_storage_uri=text_ref.uri,
                    created_at=candidate.retrieved_at,
                    units=units,
                    quality=parsed.quality,
                )
            )

        identity_candidates = self._identity_candidates(metadata)
        return IngestedRecord(
            metadata=metadata,
            assets=tuple(assets),
            documents=tuple(documents),
            quarantines=tuple(quarantines),
            acquisition_failures=acquisition_failures,
            identity_candidates=identity_candidates,
            change=change,
            decisions=tuple(decisions),
            robots=tuple(robots),
        )

    def _authorize(self, source_code: str, action: str, url: str, content_class: str) -> PolicyDecisionRecord:
        """Evaluate and record a non-network pipeline action."""
        evaluation = self._policy.evaluate(
            source_code=source_code,
            action=action,
            url=url,
            content_class=content_class,
        )
        decision = PolicyDecisionRecord(
            id=self._ids.new("pol"),
            source_code=source_code,
            policy_version=evaluation.policy_version,
            action=action,
            url=url,
            content_class=content_class,
            outcome=evaluation.outcome,
            reason=evaluation.reason,
            decided_at=self._clock.now(),
        )
        if evaluation.outcome is PolicyOutcome.DENY:
            raise PolicyDeniedError("source policy denied pipeline action", context={"reason": evaluation.reason})
        return decision

    def _identity_candidates(self, metadata: SourceMetadataRecord) -> tuple[IdentityCandidate, ...]:
        """Propose only exact-identifier matches; never auto-merge."""
        matches = self._repository.identity_matches(metadata.identifiers, exclude_record_id=metadata.id)
        candidates: list[IdentityCandidate] = []
        for match in matches:
            shared = sorted(
                set(metadata.identifiers).intersection(match.identifiers),
                key=lambda item: (item.scheme, item.value),
            )
            hard_conflict = _hard_conflict(metadata, match)
            candidates.append(
                IdentityCandidate(
                    id=stable_id("idc", *sorted((metadata.id, match.id)), "exact_identifier"),
                    left_source_record_id=metadata.id,
                    right_source_record_id=match.id,
                    method="exact_identifier",
                    confidence=1.0 if not hard_conflict else 0.5,
                    features=tuple(f"{item.scheme}:{item.value}" for item in shared),
                    hard_conflict=hard_conflict,
                    created_at=metadata.observed_at,
                )
            )
        return tuple(candidates)

    def suppress(
        self,
        *,
        scope_type: str,
        scope_value: str,
        reason: str,
        physical_deletion_requested: bool = False,
    ) -> SuppressionRecord:
        """Create an immediate durable tombstone; deletion remains an explicit job."""
        item = SuppressionRecord(
            id=self._ids.new("sup"),
            scope_type=scope_type,
            scope_value=scope_value,
            reason=reason,
            created_at=self._clock.now(),
            physical_deletion_requested=physical_deletion_requested,
        )
        self._repository.put_suppression(item)
        return item


def _rights_status(license_uri: str | None) -> str:
    """Preserve ambiguity unless a recognized open license is explicit."""
    if license_uri is None:
        return "rights_unknown"
    lower = license_uri.lower()
    is_open = any(token in lower for token in ("creativecommons.org", "opensource.org"))
    return "stored_open" if is_open else "rights_unknown"


def _hard_conflict(left: SourceMetadataRecord, right: SourceMetadataRecord) -> bool:
    """Detect conservative authorship or publication-year conflicts."""
    if (
        left.published_at is not None
        and right.published_at is not None
        and abs(left.published_at.year - right.published_at.year) > 1
    ):
        return True
    left_authors = {author.casefold() for author in left.authors}
    right_authors = {author.casefold() for author in right.authors}
    return bool(left_authors and right_authors and left_authors.isdisjoint(right_authors))
