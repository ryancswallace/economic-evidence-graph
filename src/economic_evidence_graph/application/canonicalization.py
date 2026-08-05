"""Deterministic canonical work projection from governed corpus observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit

from economic_evidence_graph.domain import (
    AccessStatus,
    CorpusSearchRecord,
    CorpusSnapshot,
    DocumentVersion,
    EvidenceBundle,
    EvidenceUnit,
    ExternalIdentifier,
    ExtractionArtifact,
    Manifestation,
    RawAsset,
    RetrievalEvent,
    SnapshotMember,
    Source,
    SourcePolicyVersion,
    TemporalSafetyClass,
    Work,
    WorkType,
    stable_id,
)
from economic_evidence_graph.exceptions import NotFoundError
from economic_evidence_graph.policy import SourceRegistry
from economic_evidence_graph.serialization import record_sha256


def canonical_work_id(record: CorpusSearchRecord) -> str:
    """Return the conservative intellectual-work identity used by every read model."""
    identities = {item.scheme.casefold(): item.value.strip().casefold() for item in record.metadata.identifiers}
    if doi := identities.get("doi"):
        normalized = doi.removeprefix("https://doi.org/").removeprefix("doi:")
        return stable_id("wrk", "doi", normalized)
    for scheme in ("repec", "handle"):
        if value := identities.get(scheme):
            return stable_id("wrk", "repec", value.removeprefix("repec:"))
    source_identifier = record.metadata.source_identifier.casefold()
    if source_identifier.startswith("repec:") or urlsplit(record.metadata.metadata_url).hostname == "ideas.repec.org":
        return stable_id("wrk", "repec", source_identifier.removeprefix("repec:"))
    return stable_id("wrk", record.metadata.source_code, source_identifier)


def canonical_version_id(record: CorpusSearchRecord) -> str:
    """Return the content-version identity shared by catalog and search projections."""
    asset_fingerprint = tuple(sorted(item.sha256 for item in record.assets)) or ("metadata_only",)
    return stable_id(
        "ver",
        record.metadata.source_code,
        record.metadata.source_identifier,
        record.metadata.metadata_sha256,
        *asset_fingerprint,
    )


class CanonicalWorkProjector:
    """Rehydrate a provenance-complete work without mutating immutable observations."""

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    def project(self, records: tuple[CorpusSearchRecord, ...], work_id: str) -> EvidenceBundle:
        """Project every unsuppressed observation matching one canonical work identifier."""
        selected = tuple(
            sorted(
                (record for record in records if not record.suppressed and canonical_work_id(record) == work_id),
                key=lambda record: (record.metadata.observed_at, record.metadata.id),
            )
        )
        if not selected:
            raise NotFoundError("work was not found", context={"work_id": work_id})

        sources, policies = self._sources(selected)
        policy_ids = {item.source_id: item.id for item in policies}
        retrievals: dict[str, RetrievalEvent] = {}
        raw_assets: dict[str, RawAsset] = {}
        versions: dict[str, DocumentVersion] = {}
        manifestations: dict[str, Manifestation] = {}
        artifacts: dict[str, ExtractionArtifact] = {}
        units: dict[str, EvidenceUnit] = {}
        members: dict[tuple[str, str], SnapshotMember] = {}

        records_by_version: dict[str, list[CorpusSearchRecord]] = defaultdict(list)
        for record in selected:
            records_by_version[canonical_version_id(record)].append(record)

        for version_id in sorted(records_by_version):
            observations = records_by_version[version_id]
            representative = observations[0]
            metadata = representative.metadata
            source_id = stable_id("src", metadata.source_code)
            policy_id = policy_ids[source_id]
            metadata_event, metadata_asset = _metadata_provenance(representative, source_id, policy_id)
            retrievals[metadata_event.id] = metadata_event
            raw_assets[metadata_asset.id] = metadata_asset
            versions[version_id] = DocumentVersion(
                id=version_id,
                work_id=work_id,
                raw_asset_id=metadata_asset.id,
                version_label=f"observed-{metadata.observed_at.isoformat()}",
                asserted_at=metadata.published_at,
                first_observed_at=metadata.observed_at,
                eligible_from=_eligible_from(representative),
                temporal_safety_class=TemporalSafetyClass.SOURCE_FACT,
            )

            version_has_document = any(record.documents for record in observations)
            for record in observations:
                record_source_id = stable_id("src", record.metadata.source_code)
                record_policy_id = policy_ids[record_source_id]
                record_metadata_event, record_metadata_asset = _metadata_provenance(
                    record, record_source_id, record_policy_id
                )
                retrievals[record_metadata_event.id] = record_metadata_event
                raw_assets[record_metadata_asset.id] = record_metadata_asset
                manifestations[stable_id("man", version_id, record.metadata.id, "metadata")] = Manifestation(
                    id=stable_id("man", version_id, record.metadata.id, "metadata"),
                    document_version_id=version_id,
                    source_id=record_source_id,
                    source_identifier=record.metadata.source_identifier,
                    source_url=record.metadata.metadata_url,
                    access_status=AccessStatus.UNRESOLVED,
                )
                assets_by_id = {asset.id: asset for asset in record.assets}
                for asset in record.assets:
                    event_id = stable_id("ret", asset.id)
                    retrievals[event_id] = RetrievalEvent(
                        id=event_id,
                        source_id=record_source_id,
                        source_policy_version_id=record_policy_id,
                        requested_url=asset.requested_url,
                        resolved_url=asset.resolved_url,
                        retrieved_at=asset.retrieved_at,
                        http_status=asset.http_status,
                        media_type=asset.media_type,
                        content_sha256=asset.sha256,
                        collector_version="governed_http_v1",
                    )
                    access_status = _access_status(asset.rights_status)
                    raw_assets[asset.id] = RawAsset(
                        id=asset.id,
                        retrieval_event_id=event_id,
                        sha256=asset.sha256,
                        media_type=asset.media_type,
                        byte_length=asset.byte_length,
                        storage_uri=asset.storage_uri,
                        access_status=access_status,
                    )
                    manifestation_id = stable_id("man", version_id, asset.id)
                    manifestations[manifestation_id] = Manifestation(
                        id=manifestation_id,
                        document_version_id=version_id,
                        source_id=record_source_id,
                        source_identifier=record.metadata.source_identifier,
                        source_url=asset.resolved_url,
                        access_status=access_status,
                        license_uri=asset.license_uri,
                    )
                for document in record.documents:
                    asset = assets_by_id[document.acquired_asset_id]
                    artifact_id = stable_id("ext", document.id)
                    artifacts[artifact_id] = ExtractionArtifact(
                        id=artifact_id,
                        document_version_id=version_id,
                        raw_asset_id=asset.id,
                        parser_name=document.parser_name,
                        parser_version=document.parser_version,
                        schema_version=document.schema_version,
                        text_sha256=document.text_sha256,
                        created_at=document.created_at,
                        temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
                    )
                    for unit in document.units:
                        evidence_id = stable_id("sev", document.id, unit.id)
                        page_start = unit.page_start or 1
                        units[evidence_id] = EvidenceUnit(
                            id=evidence_id,
                            extraction_artifact_id=artifact_id,
                            ordinal=unit.ordinal,
                            section_path=unit.section_path or ("Document",),
                            page_start=page_start,
                            page_end=unit.page_end or page_start,
                            text=unit.text,
                            temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
                        )
                    members[(version_id, artifact_id)] = SnapshotMember(work_id, version_id, artifact_id)

            if not version_has_document:
                artifact, unit = _metadata_extraction(representative, version_id, metadata_asset.id)
                artifacts[artifact.id] = artifact
                units[unit.id] = unit
                members[(version_id, artifact.id)] = SnapshotMember(work_id, version_id, artifact.id)

        latest = max(selected, key=lambda record: (record.metadata.observed_at, record.metadata.id))
        identifiers = _identifiers(selected)
        work = Work(
            id=work_id,
            title=latest.metadata.title,
            work_type=_work_type(latest.metadata.source_code),
            authors=latest.metadata.authors or ("Unknown author",),
            identifiers=identifiers,
        )
        manifest_identity = {
            "work_id": work_id,
            "source_policy_version_ids": sorted(item.id for item in policies),
            "document_version_ids": sorted(versions),
            "extraction_artifact_ids": sorted(artifacts),
            "evidence_unit_ids": sorted(units),
        }
        manifest_sha256 = record_sha256(manifest_identity)
        snapshot = CorpusSnapshot(
            id=stable_id("snapshot", "canonical-work", manifest_sha256),
            schema_version="eeg.canonical-work.v1",
            created_at=max(record.metadata.observed_at for record in selected),
            source_policy_version_ids=tuple(sorted(item.id for item in policies)),
            members=tuple(members[key] for key in sorted(members)),
            manifest_sha256=manifest_sha256,
        )
        return EvidenceBundle(
            sources=sources,
            source_policies=policies,
            retrieval_events=tuple(retrievals[key] for key in sorted(retrievals)),
            raw_assets=tuple(raw_assets[key] for key in sorted(raw_assets)),
            work=work,
            document_versions=tuple(versions[key] for key in sorted(versions)),
            manifestations=tuple(manifestations[key] for key in sorted(manifestations)),
            extraction_artifacts=tuple(artifacts[key] for key in sorted(artifacts)),
            evidence_units=tuple(units[key] for key in sorted(units)),
            evidence_records=(),
            snapshot=snapshot,
        )

    def _sources(
        self, records: tuple[CorpusSearchRecord, ...]
    ) -> tuple[tuple[Source, ...], tuple[SourcePolicyVersion, ...]]:
        """Build the exact registered sources and policy versions used by the work."""
        first_observed: dict[str, datetime] = {}
        for record in records:
            prior = first_observed.get(record.metadata.source_code)
            if prior is None or record.metadata.observed_at < prior:
                first_observed[record.metadata.source_code] = record.metadata.observed_at
        sources: list[Source] = []
        policies: list[SourcePolicyVersion] = []
        for source_code in sorted(first_observed):
            policy = self._registry.get(source_code)
            if policy is None:
                raise NotFoundError("work source is no longer registered", context={"source_code": source_code})
            source_id = stable_id("src", source_code)
            sources.append(Source(source_id, source_code, policy.display_name, policy.connector))
            policies.append(
                SourcePolicyVersion(
                    id=stable_id("spv", source_id, str(policy.policy_version)),
                    source_id=source_id,
                    version=policy.policy_version,
                    effective_at=first_observed[source_code],
                    review_status=policy.review_status,
                )
            )
        return tuple(sources), tuple(policies)


def _metadata_provenance(record: CorpusSearchRecord, source_id: str, policy_id: str) -> tuple[RetrievalEvent, RawAsset]:
    """Represent the immutable source metadata observation as canonical provenance."""
    metadata = record.metadata
    event_id = stable_id("ret", metadata.id, "metadata")
    asset_id = stable_id("raw", metadata.id, metadata.metadata_sha256)
    event = RetrievalEvent(
        id=event_id,
        source_id=source_id,
        source_policy_version_id=policy_id,
        requested_url=metadata.metadata_url,
        resolved_url=metadata.metadata_url,
        retrieved_at=metadata.observed_at,
        http_status=200,
        media_type="application/json",
        content_sha256=metadata.metadata_sha256,
        collector_version="source_connector_v1",
    )
    asset = RawAsset(
        id=asset_id,
        retrieval_event_id=event_id,
        sha256=metadata.metadata_sha256,
        media_type="application/json",
        byte_length=0,
        storage_uri=metadata.metadata_storage_uri,
        access_status=AccessStatus.RIGHTS_UNKNOWN,
    )
    return event, asset


def _metadata_extraction(
    record: CorpusSearchRecord, version_id: str, raw_asset_id: str
) -> tuple[ExtractionArtifact, EvidenceUnit]:
    """Provide a bounded metadata excerpt when no content representation parsed."""
    text = "\n\n".join(value for value in (record.metadata.title, record.metadata.abstract or "") if value.strip())
    text_sha256 = sha256(text.encode()).hexdigest()
    artifact_id = stable_id("ext", record.metadata.id, "metadata", text_sha256)
    artifact = ExtractionArtifact(
        id=artifact_id,
        document_version_id=version_id,
        raw_asset_id=raw_asset_id,
        parser_name="metadata",
        parser_version="1",
        schema_version="eeg.canonical-work.v1",
        text_sha256=text_sha256,
        created_at=record.metadata.observed_at,
        temporal_safety_class=TemporalSafetyClass.SOURCE_FACT,
    )
    unit = EvidenceUnit(
        id=stable_id("mev", artifact_id),
        extraction_artifact_id=artifact_id,
        ordinal=0,
        section_path=("Metadata",),
        page_start=1,
        page_end=1,
        text=text,
        temporal_safety_class=TemporalSafetyClass.SOURCE_FACT,
    )
    return artifact, unit


def _identifiers(records: tuple[CorpusSearchRecord, ...]) -> tuple[ExternalIdentifier, ...]:
    """Union source identifiers without conflating differently cased values."""
    values = {
        (item.scheme.casefold(), item.value.strip()): item for record in records for item in record.metadata.identifiers
    }
    return tuple(
        ExternalIdentifier(stable_id("xid", scheme, value.casefold()), scheme, value)
        for scheme, value in sorted(values)
    )


def _access_status(rights_status: str) -> AccessStatus:
    """Map the governed corpus rights vocabulary into the canonical access vocabulary."""
    return AccessStatus.STORED_OPEN if rights_status == "stored_open" else AccessStatus.RIGHTS_UNKNOWN


def _work_type(source_code: str) -> WorkType:
    """Assign a conservative source-level type when metadata has no richer type field."""
    if source_code == "board_fomc":
        return WorkType.POLICY_DOCUMENT
    if source_code == "crossref":
        return WorkType.JOURNAL_ARTICLE
    if source_code in {"repec", "core", "boston_fed"}:
        return WorkType.WORKING_PAPER
    return WorkType.OTHER


def _eligible_from(record: CorpusSearchRecord) -> datetime:
    """Match the strict T0 work-availability rule used by search projection."""
    if record.metadata.source_code == "board_fomc" and record.metadata.asserted_updated_at is not None:
        return record.metadata.asserted_updated_at
    return record.metadata.observed_at
