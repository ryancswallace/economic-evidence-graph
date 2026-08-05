"""Deterministic corpus projection and immutable search-index activation."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from economic_evidence_graph.application.canonicalization import canonical_version_id, canonical_work_id
from economic_evidence_graph.domain import (
    AvailabilityDecision,
    CorpusSearchRecord,
    IndexGeneration,
    SearchEvidenceDocument,
    SearchWorkDocument,
    TemporalSafetyClass,
    stable_id,
)
from economic_evidence_graph.policy import SourceRegistry
from economic_evidence_graph.ports import Clock
from economic_evidence_graph.ports.search import EmbeddingProvider, SearchIndex, SearchRepository
from economic_evidence_graph.serialization import record_sha256

MAPPING_VERSION = "eeg.search-mapping.v1"
RETRIEVAL_CONFIG_ID = "eeg.hybrid-rrf.v1"


class SearchProjector:
    """Build search read models without mutating canonical Step 2 records."""

    def __init__(self, *, registry: SourceRegistry, embeddings: EmbeddingProvider) -> None:
        self._registry = registry
        self._embeddings = embeddings

    def project(
        self, records: tuple[CorpusSearchRecord, ...]
    ) -> tuple[tuple[SearchWorkDocument, ...], tuple[SearchEvidenceDocument, ...]]:
        """Project unsuppressed source observations into versioned documents."""
        work_documents: list[SearchWorkDocument] = []
        evidence_documents: list[SearchEvidenceDocument] = []
        prepared: list[tuple[CorpusSearchRecord, str, str, AvailabilityDecision]] = []
        counts: dict[str, set[str]] = defaultdict(set)
        repeated_units: dict[tuple[str, str], set[str]] = defaultdict(set)

        for record in records:
            if record.suppressed:
                continue
            work_id = canonical_work_id(record)
            version_id = canonical_version_id(record)
            availability = availability_decision(record)
            prepared.append((record, work_id, version_id, availability))
            counts[work_id].add(version_id)
            for document in record.documents:
                for unit in document.units:
                    repeated_units[(record.metadata.source_code, _normalized_unit_text(unit.text))].add(work_id)

        emitted_versions: set[str] = set()
        emitted_evidence: set[tuple[str, str, str, str]] = set()
        for record, work_id, version_id, availability in sorted(
            prepared, key=lambda item: (item[0].metadata.observed_at, item[0].metadata.id)
        ):
            policy = self._registry.get(record.metadata.source_code)
            if policy is None:
                continue
            assets_by_id = {item.id: item for item in record.assets}
            warnings = tuple(sorted({warning for item in record.documents for warning in item.quality.warnings}))
            rights = tuple(sorted({item.rights_status for item in record.assets})) or ("metadata_only",)
            normalized_ids = tuple(sorted(item.id for item in record.documents))
            asset_ids = tuple(sorted(item.id for item in record.assets))
            abstract = record.metadata.abstract
            work_text = "\n".join(
                value
                for value in (
                    record.metadata.title,
                    " ".join(record.metadata.authors),
                    abstract or "",
                )
                if value
            )
            if version_id not in emitted_versions:
                work_documents.append(
                    SearchWorkDocument(
                        id=stable_id("swork", version_id),
                        work_id=work_id,
                        document_version_id=version_id,
                        source_record_id=record.metadata.id,
                        source_code=record.metadata.source_code,
                        source_identifier=record.metadata.source_identifier,
                        source_url=record.metadata.metadata_url,
                        title=record.metadata.title,
                        authors=record.metadata.authors,
                        abstract=abstract,
                        identifiers=tuple(
                            sorted(
                                f"{item.scheme.casefold()}:{item.value.casefold()}"
                                for item in record.metadata.identifiers
                            )
                        ),
                        published_at=record.metadata.published_at,
                        observed_at=record.metadata.observed_at,
                        eligible_from=availability.eligible_from,
                        date_basis=availability.basis,
                        date_evidence=availability.evidence,
                        date_uncertainty=availability.uncertainty,
                        temporal_safety_class=TemporalSafetyClass.SOURCE_FACT,
                        policy_version=policy.policy_version,
                        normalized_document_ids=normalized_ids,
                        asset_ids=asset_ids,
                        rights_statuses=rights,
                        quality_warnings=warnings,
                        version_count=len(counts[work_id]),
                        embedding=self._embeddings.embed(work_text),
                    )
                )
                emitted_versions.add(version_id)
            for document in record.documents:
                asset = assets_by_id.get(document.acquired_asset_id)
                if asset is None:
                    continue
                for unit in document.units:
                    repetition_count = len(
                        repeated_units[(record.metadata.source_code, _normalized_unit_text(unit.text))]
                    )
                    if not _is_substantive_evidence(unit.text) or _is_repeated_boilerplate(unit.text, repetition_count):
                        continue
                    evidence_key = (version_id, asset.sha256, unit.locator, document.text_sha256)
                    if evidence_key in emitted_evidence:
                        continue
                    emitted_evidence.add(evidence_key)
                    evidence_availability = _evidence_availability(availability, asset.retrieved_at)
                    evidence_documents.append(
                        SearchEvidenceDocument(
                            id=stable_id("sev", document.id, unit.id),
                            work_id=work_id,
                            document_version_id=version_id,
                            source_record_id=record.metadata.id,
                            normalized_document_id=document.id,
                            acquired_asset_id=asset.id,
                            source_code=record.metadata.source_code,
                            source_identifier=record.metadata.source_identifier,
                            source_url=record.metadata.metadata_url,
                            content_url=asset.resolved_url,
                            title=record.metadata.title,
                            authors=record.metadata.authors,
                            published_at=record.metadata.published_at,
                            observed_at=record.metadata.observed_at,
                            eligible_from=evidence_availability.eligible_from,
                            date_basis=evidence_availability.basis,
                            date_evidence=evidence_availability.evidence,
                            date_uncertainty=evidence_availability.uncertainty,
                            temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
                            policy_version=policy.policy_version,
                            text=unit.text,
                            section_path=unit.section_path,
                            locator=unit.locator,
                            page_start=unit.page_start,
                            page_end=unit.page_end,
                            parser_name=document.parser_name,
                            parser_version=document.parser_version,
                            extraction_schema_version=document.schema_version,
                            text_sha256=document.text_sha256,
                            content_sha256=asset.sha256,
                            retrieved_at=asset.retrieved_at,
                            rights_status=asset.rights_status,
                            quality_warnings=document.quality.warnings,
                            version_count=len(counts[work_id]),
                            embedding=self._embeddings.embed(
                                f"{record.metadata.title}\n{' '.join(unit.section_path)}\n{unit.text}"
                            ),
                        )
                    )
        return (
            tuple(sorted(work_documents, key=lambda item: item.id)),
            tuple(sorted(evidence_documents, key=lambda item: item.id)),
        )


def _normalized_unit_text(text: str) -> str:
    """Normalize evidence text for cross-document boilerplate detection."""
    return " ".join(text.casefold().split())


def _is_substantive_evidence(text: str) -> bool:
    """Require enough standalone context for an evidence search result."""
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    has_sentence_boundary = re.search(r"[.!?]", text) is not None
    return len(text.strip()) >= 32 and len(words) >= 4 and (len(text) > 80 or has_sentence_boundary)


def _is_repeated_boilerplate(text: str, repetition_count: int) -> bool:
    """Reject repeated short labels while retaining repeated substantive sentences."""
    if repetition_count < 3 or len(text) > 200:
        return False
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    return len(words) <= 8 or re.search(r"[.!?]", text) is None


class SearchIndexer:
    """Build, validate, activate, and record one immutable generation."""

    def __init__(
        self,
        *,
        repository: SearchRepository,
        index: SearchIndex,
        projector: SearchProjector,
        embeddings: EmbeddingProvider,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._index = index
        self._projector = projector
        self._embeddings = embeddings
        self._clock = clock

    def rebuild(self) -> IndexGeneration:
        """Rebuild from the complete immutable local corpus and activate it."""
        works, evidence = self._projector.project(self._repository.list_corpus_search_records())
        manifest = record_sha256(
            {
                "mapping_version": MAPPING_VERSION,
                "retrieval_config_id": RETRIEVAL_CONFIG_ID,
                "embedding_config_id": self._embeddings.config_id,
                "work_documents": works,
                "evidence_documents": evidence,
            }
        )
        now = self._clock.now()
        generation_id = stable_id("idxgen", manifest, now.isoformat())
        suffix = f"{manifest[:12]}-{generation_id[-8:]}"
        generation = IndexGeneration(
            id=generation_id,
            mapping_version=MAPPING_VERSION,
            retrieval_config_id=RETRIEVAL_CONFIG_ID,
            embedding_config_id=self._embeddings.config_id,
            corpus_snapshot_id=stable_id("snapshot", "step2-search-projection", manifest),
            manifest_sha256=manifest,
            work_index_name=f"eeg-work-{suffix}",
            evidence_index_name=f"eeg-evidence-{suffix}",
            work_document_count=len(works),
            evidence_document_count=len(evidence),
            created_at=now,
            activated_at=now,
        )
        existing = self._repository.get_index_generation(generation_id)
        if existing is not None:
            generation = existing
        self._index.replace_generation(
            generation,
            works,
            evidence,
            embedding_dimension=self._embeddings.dimension,
        )
        if existing is None:
            self._repository.put_index_generation(generation)
        return generation


def availability_decision(record: CorpusSearchRecord) -> AvailabilityDecision:
    """Choose the earliest conservative T0 availability evidence."""
    metadata = record.metadata
    if metadata.source_code == "board_fomc" and metadata.asserted_updated_at is not None:
        return AvailabilityDecision(
            metadata.asserted_updated_at,
            "official_release_timestamp",
            metadata.asserted_updated_at.isoformat(),
            None,
        )
    assertions = [
        value
        for value in (
            None if metadata.published_at is None else f"published_at={metadata.published_at.isoformat()}",
            None
            if metadata.asserted_updated_at is None
            else f"asserted_updated_at={metadata.asserted_updated_at.isoformat()}",
        )
        if value is not None
    ]
    return AvailabilityDecision(
        metadata.observed_at,
        "first_observed_timestamp",
        metadata.observed_at.isoformat(),
        None if not assertions else f"source assertions not used as strict observations: {', '.join(assertions)}",
    )


def _evidence_availability(metadata_availability: AvailabilityDecision, retrieved_at: datetime) -> AvailabilityDecision:
    """Never backdate exact document bytes before their first retrieval."""
    if retrieved_at <= metadata_availability.eligible_from:
        return metadata_availability
    return AvailabilityDecision(
        retrieved_at,
        "content_first_observed_timestamp",
        retrieved_at.isoformat(),
        "document bytes were not independently archived at the earlier metadata date",
    )
