"""Transactional SQL repository for PostgreSQL and SQLite contract tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, TypeVar, cast

from sqlalchemy import Connection, Engine, RowMapping, Table, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from economic_evidence_graph.domain import (
    AccessStatus,
    AcquiredAsset,
    AgentToolName,
    ContentLink,
    CorpusSearchRecord,
    CorpusSnapshot,
    DocumentVersion,
    EvidenceAnchor,
    EvidenceBundle,
    EvidenceFieldRevision,
    EvidenceLineageMethod,
    EvidenceRecord,
    EvidenceState,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    EvidenceUnit,
    ExternalIdentifier,
    ExtractionArtifact,
    IdentifierValue,
    IndexGeneration,
    IngestedRecord,
    Job,
    JobState,
    Manifestation,
    NormalizedDocument,
    NormalizedUnit,
    PolicyDecisionRecord,
    PolicyLink,
    PolicyLinkType,
    Project,
    ProjectBundle,
    ProjectEvidenceAnchor,
    ProjectEvidenceField,
    ProjectEvidenceRecord,
    ProjectExport,
    ProjectExportFormat,
    ProjectMember,
    ProjectNote,
    ProjectRole,
    ProjectSearchRun,
    QualityReport,
    RawAsset,
    RetrievalEvent,
    ReviewEvent,
    ReviewTargetType,
    RobotsObservation,
    ScreeningEvent,
    ScreeningOutcome,
    SearchRun,
    SnapshotMember,
    Source,
    SourceMetadataRecord,
    SourcePolicyVersion,
    SuppressionRecord,
    TemporalSafetyClass,
    ToolInvocationOutcome,
    ToolInvocationRecord,
    Work,
    WorkType,
    stable_id,
)
from economic_evidence_graph.domain.models import ScalarValue
from economic_evidence_graph.exceptions import ConflictError, NotFoundError
from economic_evidence_graph.providers.postgres.schema import (
    acquired_asset,
    acquisition_failure,
    corpus_snapshot,
    document_version,
    evidence_anchor,
    evidence_field_revision,
    evidence_record,
    evidence_table,
    evidence_table_row,
    evidence_unit,
    external_identifier,
    extraction_artifact,
    identity_candidate,
    index_generation,
    job,
    manifestation,
    normalized_document,
    normalized_unit,
    policy_decision,
    policy_link,
    project,
    project_evidence_record,
    project_export,
    project_member,
    project_note,
    project_search_run,
    quarantine_record,
    raw_asset,
    retrieval_event,
    review_event,
    robots_observation,
    screening_event,
    search_run,
    snapshot_member,
    source,
    source_change_event,
    source_metadata_record,
    source_policy_version,
    suppression_record,
    tool_invocation,
    work,
)
from economic_evidence_graph.serialization import JsonValue, record_sha256, to_json_value

T = TypeVar("T")


class SqlRepository:
    """Canonical catalog and job repository using explicit SQL transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def close(self) -> None:
        """Dispose the owned SQLAlchemy connection pool."""
        self._engine.dispose()

    def current_migration_revision(self) -> str | None:
        """Return the database's exact Alembic revision without mutating it."""
        with self._engine.connect() as connection:
            try:
                value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            except SQLAlchemyError:  # absent version table is an explicit unversioned state
                return None
        return None if value is None else str(value)

    def put_bundle(self, bundle: EvidenceBundle) -> None:
        """Persist one complete evidence bundle atomically and idempotently."""
        bundle.validate_lineage()
        with self._engine.begin() as connection:
            for item in bundle.sources:
                _put_immutable(
                    connection,
                    source,
                    item.id,
                    {
                        "id": item.id,
                        "code": item.code,
                        "display_name": item.display_name,
                        "source_kind": item.source_kind,
                    },
                    item,
                )
            for item in bundle.source_policies:
                _put_immutable(
                    connection,
                    source_policy_version,
                    item.id,
                    {
                        "id": item.id,
                        "source_id": item.source_id,
                        "version": item.version,
                        "effective_at": item.effective_at,
                        "review_status": item.review_status,
                    },
                    item,
                )
            for item in bundle.retrieval_events:
                _put_immutable(
                    connection,
                    retrieval_event,
                    item.id,
                    {
                        "id": item.id,
                        "source_id": item.source_id,
                        "source_policy_version_id": item.source_policy_version_id,
                        "requested_url": item.requested_url,
                        "resolved_url": item.resolved_url,
                        "retrieved_at": item.retrieved_at,
                        "http_status": item.http_status,
                        "media_type": item.media_type,
                        "content_sha256": item.content_sha256,
                        "collector_version": item.collector_version,
                    },
                    item,
                )
            for item in bundle.raw_assets:
                _put_immutable(
                    connection,
                    raw_asset,
                    item.id,
                    {
                        "id": item.id,
                        "retrieval_event_id": item.retrieval_event_id,
                        "sha256": item.sha256,
                        "media_type": item.media_type,
                        "byte_length": item.byte_length,
                        "storage_uri": item.storage_uri,
                        "access_status": item.access_status.value,
                    },
                    item,
                )
            _put_immutable(
                connection,
                work,
                bundle.work.id,
                {
                    "id": bundle.work.id,
                    "title": bundle.work.title,
                    "work_type": bundle.work.work_type.value,
                    "authors": list(bundle.work.authors),
                },
                bundle.work,
            )
            for identifier in bundle.work.identifiers:
                _put_immutable(
                    connection,
                    external_identifier,
                    identifier.id,
                    {
                        "id": identifier.id,
                        "work_id": bundle.work.id,
                        "scheme": identifier.scheme,
                        "value": identifier.value,
                    },
                    {"work_id": bundle.work.id, "identifier": identifier},
                )
            for item in bundle.document_versions:
                _put_immutable(
                    connection,
                    document_version,
                    item.id,
                    {
                        "id": item.id,
                        "work_id": item.work_id,
                        "raw_asset_id": item.raw_asset_id,
                        "version_label": item.version_label,
                        "asserted_at": item.asserted_at,
                        "first_observed_at": item.first_observed_at,
                        "eligible_from": item.eligible_from,
                        "temporal_safety_class": item.temporal_safety_class.value,
                    },
                    item,
                )
            for item in bundle.manifestations:
                _put_immutable(
                    connection,
                    manifestation,
                    item.id,
                    {
                        "id": item.id,
                        "document_version_id": item.document_version_id,
                        "source_id": item.source_id,
                        "source_identifier": item.source_identifier,
                        "source_url": item.source_url,
                        "access_status": item.access_status.value,
                        "license_uri": item.license_uri,
                    },
                    item,
                )
            for item in bundle.extraction_artifacts:
                _put_immutable(
                    connection,
                    extraction_artifact,
                    item.id,
                    {
                        "id": item.id,
                        "document_version_id": item.document_version_id,
                        "raw_asset_id": item.raw_asset_id,
                        "parser_name": item.parser_name,
                        "parser_version": item.parser_version,
                        "schema_version": item.schema_version,
                        "text_sha256": item.text_sha256,
                        "created_at": item.created_at,
                        "temporal_safety_class": item.temporal_safety_class.value,
                    },
                    item,
                )
            for item in bundle.evidence_units:
                _put_immutable(
                    connection,
                    evidence_unit,
                    item.id,
                    {
                        "id": item.id,
                        "extraction_artifact_id": item.extraction_artifact_id,
                        "ordinal": item.ordinal,
                        "section_path": list(item.section_path),
                        "page_start": item.page_start,
                        "page_end": item.page_end,
                        "text": item.text,
                        "temporal_safety_class": item.temporal_safety_class.value,
                    },
                    item,
                )
            for item in bundle.evidence_records:
                self._put_evidence_record(connection, item)
            _put_immutable(
                connection,
                corpus_snapshot,
                bundle.snapshot.id,
                {
                    "id": bundle.snapshot.id,
                    "schema_version": bundle.snapshot.schema_version,
                    "created_at": bundle.snapshot.created_at,
                    "source_policy_version_ids": list(bundle.snapshot.source_policy_version_ids),
                    "manifest_sha256": bundle.snapshot.manifest_sha256,
                    "sealed": True,
                },
                bundle.snapshot,
            )
            for member in bundle.snapshot.members:
                member_id = stable_id(
                    "snm",
                    bundle.snapshot.id,
                    member.work_id,
                    member.document_version_id,
                    member.extraction_artifact_id,
                )
                _put_immutable(
                    connection,
                    snapshot_member,
                    member_id,
                    {
                        "id": member_id,
                        "snapshot_id": bundle.snapshot.id,
                        "work_id": member.work_id,
                        "document_version_id": member.document_version_id,
                        "extraction_artifact_id": member.extraction_artifact_id,
                    },
                    {"snapshot_id": bundle.snapshot.id, "member": member},
                )

    def get_bundle(self, work_id: str) -> EvidenceBundle:
        """Rehydrate one work and all provenance needed to interpret it."""
        with self._engine.connect() as connection:
            work_row = connection.execute(select(work).where(work.c.id == work_id)).mappings().one_or_none()
            if work_row is None:
                raise NotFoundError("work was not found", context={"work_id": work_id})

            version_rows = _rows(connection, select(document_version).where(document_version.c.work_id == work_id))
            version_ids = [str(row["id"]) for row in version_rows]
            asset_ids = [str(row["raw_asset_id"]) for row in version_rows]
            asset_rows = _rows_in(connection, raw_asset, raw_asset.c.id, asset_ids)
            retrieval_ids = [str(row["retrieval_event_id"]) for row in asset_rows]
            retrieval_rows = _rows_in(connection, retrieval_event, retrieval_event.c.id, retrieval_ids)
            manifestation_rows = _rows_in(
                connection,
                manifestation,
                manifestation.c.document_version_id,
                version_ids,
            )
            extraction_rows = _rows_in(
                connection,
                extraction_artifact,
                extraction_artifact.c.document_version_id,
                version_ids,
            )
            extraction_ids = [str(row["id"]) for row in extraction_rows]
            unit_rows = _rows_in(connection, evidence_unit, evidence_unit.c.extraction_artifact_id, extraction_ids)
            record_rows = _rows_in(connection, evidence_record, evidence_record.c.document_version_id, version_ids)

            snapshot_row = (
                connection.execute(
                    select(corpus_snapshot)
                    .join(snapshot_member, snapshot_member.c.snapshot_id == corpus_snapshot.c.id)
                    .where(snapshot_member.c.work_id == work_id)
                    .order_by(corpus_snapshot.c.created_at.desc(), corpus_snapshot.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if snapshot_row is None:
                raise NotFoundError("work has no corpus snapshot", context={"work_id": work_id})
            snapshot_id = str(snapshot_row["id"])
            member_rows = _rows(connection, select(snapshot_member).where(snapshot_member.c.snapshot_id == snapshot_id))

            policy_ids = {str(row["source_policy_version_id"]) for row in retrieval_rows}
            policy_ids.update(cast(list[str], snapshot_row["source_policy_version_ids"]))
            policy_rows = _rows_in(connection, source_policy_version, source_policy_version.c.id, policy_ids)
            source_ids = {str(row["source_id"]) for row in retrieval_rows}
            source_ids.update(str(row["source_id"]) for row in manifestation_rows)
            source_ids.update(str(row["source_id"]) for row in policy_rows)
            source_rows = _rows_in(connection, source, source.c.id, source_ids)

            identifiers = tuple(
                _external_identifier(row)
                for row in _rows(
                    connection,
                    select(external_identifier).where(external_identifier.c.work_id == work_id),
                )
            )
            domain_work = Work(
                id=str(work_row["id"]),
                title=str(work_row["title"]),
                work_type=WorkType(str(work_row["work_type"])),
                authors=tuple(cast(list[str], work_row["authors"])),
                identifiers=identifiers,
            )
            return EvidenceBundle(
                sources=tuple(_source(row) for row in source_rows),
                source_policies=tuple(_source_policy(row) for row in policy_rows),
                retrieval_events=tuple(_retrieval(row) for row in retrieval_rows),
                raw_assets=tuple(_raw_asset(row) for row in asset_rows),
                work=domain_work,
                document_versions=tuple(_document_version(row) for row in version_rows),
                manifestations=tuple(_manifestation(row) for row in manifestation_rows),
                extraction_artifacts=tuple(_extraction(row) for row in extraction_rows),
                evidence_units=tuple(_evidence_unit(row) for row in unit_rows),
                evidence_records=self._evidence_records(connection, record_rows),
                snapshot=_snapshot(snapshot_row, member_rows),
            )

    def add(self, item: Job) -> None:
        """Create a new pending job."""
        if item.state is not JobState.PENDING:
            raise ConflictError("new jobs must be pending")
        values = _job_values(item)
        values["revision"] = 1
        values["record_sha256"] = record_sha256(item)
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(job).values(**values))
        except IntegrityError as error:
            raise ConflictError("job identifier or idempotency key already exists") from error

    def save(self, item: Job) -> None:
        """Persist a valid next job state with optimistic revision checking."""
        with self._engine.begin() as connection:
            row = connection.execute(select(job).where(job.c.id == item.id)).mappings().one_or_none()
            if row is None:
                raise NotFoundError("job was not found", context={"job_id": item.id})
            prior = _job(row)
            if record_sha256(prior) == record_sha256(item):
                return
            allowed = {
                JobState.PENDING: {JobState.RUNNING},
                JobState.RUNNING: {JobState.SUCCEEDED, JobState.FAILED},
                JobState.SUCCEEDED: set(),
                JobState.FAILED: set(),
            }
            if item.state not in allowed[prior.state] or item.idempotency_key != prior.idempotency_key:
                raise ConflictError("job revision is not a valid next state")
            prior_revision = int(row["revision"])
            values = _job_values(item)
            values["revision"] = prior_revision + 1
            values["record_sha256"] = record_sha256(item)
            result = connection.execute(
                update(job).where(job.c.id == item.id, job.c.revision == prior_revision).values(**values)
            )
            if result.rowcount != 1:
                raise ConflictError("job was modified concurrently")

    def get(self, job_id: str) -> Job:
        """Return a job by identifier."""
        with self._engine.connect() as connection:
            row = connection.execute(select(job).where(job.c.id == job_id)).mappings().one_or_none()
        if row is None:
            raise NotFoundError("job was not found", context={"job_id": job_id})
        return _job(row)

    def find_by_idempotency_key(self, key: str) -> Job | None:
        """Return a prior job for the idempotency key."""
        with self._engine.connect() as connection:
            row = connection.execute(select(job).where(job.c.idempotency_key == key)).mappings().one_or_none()
        return None if row is None else _job(row)

    def check(self) -> None:
        """Confirm the database responds to a minimal query."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    def put_ingested_record(self, item: IngestedRecord) -> None:
        """Persist one complete Step 2 result atomically and idempotently."""
        with self._engine.begin() as connection:
            self._put_governance_trace(connection, item.decisions, item.robots)
            _put_immutable(
                connection,
                source_metadata_record,
                item.metadata.id,
                {
                    "id": item.metadata.id,
                    "source_code": item.metadata.source_code,
                    "source_identifier": item.metadata.source_identifier,
                    "metadata_sha256": item.metadata.metadata_sha256,
                    "observed_at": item.metadata.observed_at,
                    "payload": to_json_value(item.metadata),
                },
                item.metadata,
            )
            for asset in item.assets:
                _put_immutable(
                    connection,
                    acquired_asset,
                    asset.id,
                    {
                        "id": asset.id,
                        "source_record_id": asset.source_record_id,
                        "sha256": asset.sha256,
                        "byte_length": asset.byte_length,
                        "payload": to_json_value(asset),
                    },
                    asset,
                )
            for failure in item.acquisition_failures:
                _put_immutable(
                    connection,
                    acquisition_failure,
                    failure.id,
                    {
                        "id": failure.id,
                        "source_record_id": failure.source_record_id,
                        "requested_url": failure.requested_url,
                        "failure_code": failure.failure_code,
                        "occurred_at": failure.occurred_at,
                        "payload": to_json_value(failure),
                    },
                    failure,
                )
            for document in item.documents:
                _put_immutable(
                    connection,
                    normalized_document,
                    document.id,
                    {
                        "id": document.id,
                        "acquired_asset_id": document.acquired_asset_id,
                        "text_sha256": document.text_sha256,
                        "payload": to_json_value(document),
                    },
                    document,
                )
                for unit in document.units:
                    _put_immutable(
                        connection,
                        normalized_unit,
                        unit.id,
                        {
                            "id": unit.id,
                            "normalized_document_id": document.id,
                            "ordinal": unit.ordinal,
                            "payload": to_json_value(unit),
                        },
                        unit,
                    )
            for quarantine in item.quarantines:
                _put_immutable(
                    connection,
                    quarantine_record,
                    quarantine.id,
                    {
                        "id": quarantine.id,
                        "acquired_asset_id": quarantine.acquired_asset_id,
                        "reason": quarantine.reason.value,
                        "retryable": quarantine.retryable,
                        "payload": to_json_value(quarantine),
                    },
                    quarantine,
                )
            for candidate in item.identity_candidates:
                _put_immutable(
                    connection,
                    identity_candidate,
                    candidate.id,
                    {
                        "id": candidate.id,
                        "left_source_record_id": candidate.left_source_record_id,
                        "right_source_record_id": candidate.right_source_record_id,
                        "method": candidate.method,
                        "confidence": candidate.confidence,
                        "hard_conflict": candidate.hard_conflict,
                        "payload": to_json_value(candidate),
                    },
                    candidate,
                )
            _put_immutable(
                connection,
                source_change_event,
                item.change.id,
                {
                    "id": item.change.id,
                    "source_code": item.change.source_code,
                    "source_identifier": item.change.source_identifier,
                    "current_record_id": item.change.current_record_id,
                    "kind": item.change.kind.value,
                    "payload": to_json_value(item.change),
                },
                item.change,
            )

    def put_governance_trace(
        self,
        decisions: tuple[PolicyDecisionRecord, ...],
        robots: tuple[RobotsObservation, ...],
    ) -> None:
        """Persist discovery policy and robots observations idempotently."""
        with self._engine.begin() as connection:
            self._put_governance_trace(connection, decisions, robots)

    def latest_source_record(self, source_code: str, source_identifier: str) -> SourceMetadataRecord | None:
        """Return the latest matching source metadata observation."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(source_metadata_record)
                    .where(
                        source_metadata_record.c.source_code == source_code,
                        source_metadata_record.c.source_identifier == source_identifier,
                    )
                    .order_by(source_metadata_record.c.observed_at.desc(), source_metadata_record.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _source_metadata(cast(dict[str, Any], row["payload"]))

    def identity_matches(
        self,
        identifiers: tuple[IdentifierValue, ...],
        *,
        exclude_record_id: str,
    ) -> tuple[SourceMetadataRecord, ...]:
        """Return records sharing exact identifiers using portable JSON rehydration."""
        needles = set(identifiers)
        if not needles:
            return ()
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(source_metadata_record.c.payload).where(source_metadata_record.c.id != exclude_record_id)
            ).scalars()
            records = (_source_metadata(cast(dict[str, Any], payload)) for payload in rows)
            matches = {record.id: record for record in records if needles.intersection(record.identifiers)}
        return tuple(matches[key] for key in sorted(matches))

    def put_suppression(self, item: SuppressionRecord) -> None:
        """Persist an immutable suppression tombstone."""
        with self._engine.begin() as connection:
            _put_immutable(
                connection,
                suppression_record,
                item.id,
                {
                    "id": item.id,
                    "scope_type": item.scope_type,
                    "scope_value": item.scope_value,
                    "reason": item.reason,
                    "created_at": item.created_at,
                    "physical_deletion_requested": item.physical_deletion_requested,
                },
                item,
            )

    def is_suppressed(self, scope_type: str, scope_value: str) -> bool:
        """Return whether a matching suppression tombstone exists."""
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    select(suppression_record.c.id)
                    .where(
                        suppression_record.c.scope_type == scope_type,
                        suppression_record.c.scope_value == scope_value,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            )

    def corpus_usage(self) -> tuple[int, int]:
        """Return persisted source-record count and raw acquired bytes."""
        with self._engine.connect() as connection:
            record_count = connection.execute(select(func.count()).select_from(source_metadata_record)).scalar_one()
            byte_count = connection.execute(
                select(func.coalesce(func.sum(acquired_asset.c.byte_length), 0))
            ).scalar_one()
        return int(record_count), int(byte_count)

    def list_corpus_search_records(self) -> tuple[CorpusSearchRecord, ...]:
        """Return joined Step 2 observations for deterministic search projection."""
        with self._engine.connect() as connection:
            metadata_payloads = list(connection.execute(select(source_metadata_record.c.payload)).scalars())
            asset_payloads = list(connection.execute(select(acquired_asset.c.payload)).scalars())
            document_payloads = list(connection.execute(select(normalized_document.c.payload)).scalars())
            suppression_rows = list(
                connection.execute(select(suppression_record.c.scope_type, suppression_record.c.scope_value)).mappings()
            )

        metadata_items = {
            item.id: item for item in (_source_metadata(cast(dict[str, Any], value)) for value in metadata_payloads)
        }
        assets_by_record: dict[str, list[AcquiredAsset]] = {}
        assets_by_id: dict[str, AcquiredAsset] = {}
        for item in (_acquired_asset(cast(dict[str, Any], value)) for value in asset_payloads):
            assets_by_record.setdefault(item.source_record_id, []).append(item)
            assets_by_id[item.id] = item
        documents_by_record: dict[str, list[NormalizedDocument]] = {}
        for item in (_normalized_document(cast(dict[str, Any], value)) for value in document_payloads):
            asset = assets_by_id.get(item.acquired_asset_id)
            if asset is not None:
                documents_by_record.setdefault(asset.source_record_id, []).append(item)

        suppressed = {(str(row["scope_type"]), str(row["scope_value"])) for row in suppression_rows}
        records: list[CorpusSearchRecord] = []
        for record_id in sorted(metadata_items):
            item = metadata_items[record_id]
            assets = tuple(sorted(assets_by_record.get(record_id, []), key=lambda value: value.id))
            documents = tuple(sorted(documents_by_record.get(record_id, []), key=lambda value: value.id))
            source_key = f"{item.source_code}:{item.source_identifier}"
            blocked = ("source_record", source_key) in suppressed or ("url", item.metadata_url) in suppressed
            blocked = blocked or any(
                ("asset", asset.id) in suppressed
                or ("url", asset.requested_url) in suppressed
                or ("url", asset.resolved_url) in suppressed
                for asset in assets
            )
            records.append(CorpusSearchRecord(item, assets, documents, blocked))
        return tuple(records)

    def list_suppression_scopes(self) -> tuple[tuple[str, str], ...]:
        """Return current suppression scopes in deterministic order."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(suppression_record.c.scope_type, suppression_record.c.scope_value).order_by(
                    suppression_record.c.scope_type, suppression_record.c.scope_value
                )
            )
        return tuple((str(row.scope_type), str(row.scope_value)) for row in rows)

    def put_index_generation(self, item: IndexGeneration) -> None:
        """Persist an activated immutable index generation."""
        with self._engine.begin() as connection:
            _put_immutable(
                connection,
                index_generation,
                item.id,
                {
                    "id": item.id,
                    "mapping_version": item.mapping_version,
                    "retrieval_config_id": item.retrieval_config_id,
                    "embedding_config_id": item.embedding_config_id,
                    "corpus_snapshot_id": item.corpus_snapshot_id,
                    "manifest_sha256": item.manifest_sha256,
                    "work_index_name": item.work_index_name,
                    "evidence_index_name": item.evidence_index_name,
                    "work_document_count": item.work_document_count,
                    "evidence_document_count": item.evidence_document_count,
                    "created_at": item.created_at,
                    "activated_at": item.activated_at,
                    "payload": to_json_value(item),
                },
                item,
            )

    def latest_index_generation(self) -> IndexGeneration | None:
        """Return the most recently activated search generation."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(index_generation.c.payload)
                .order_by(index_generation.c.activated_at.desc(), index_generation.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        return None if payload is None else _index_generation(cast(dict[str, Any], payload))

    def get_index_generation(self, generation_id: str) -> IndexGeneration | None:
        """Return one immutable search generation."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(index_generation.c.payload).where(index_generation.c.id == generation_id)
            ).scalar_one_or_none()
        return None if payload is None else _index_generation(cast(dict[str, Any], payload))

    def put_search_run(self, item: SearchRun) -> None:
        """Persist one immutable search-run manifest."""
        with self._engine.begin() as connection:
            _put_immutable(
                connection,
                search_run,
                item.id,
                {
                    "id": item.id,
                    "index_generation_id": item.index_generation_id,
                    "query_sha256": item.query_sha256,
                    "created_at": item.created_at,
                    "payload": to_json_value(item),
                },
                item,
            )

    def get_search_run(self, run_id: str) -> SearchRun | None:
        """Return one immutable search-run manifest."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(search_run.c.payload).where(search_run.c.id == run_id)
            ).scalar_one_or_none()
        return None if payload is None else _search_run(cast(dict[str, Any], payload))

    def create_project(self, item: Project, owner: ProjectMember) -> Project:
        """Create a project and its owner membership atomically and idempotently."""
        if owner.project_id != item.id or owner.actor_id != item.created_by:
            raise ConflictError("project owner attribution does not match its creator")
        with self._engine.begin() as connection:
            prior_payload = connection.execute(
                select(project.c.payload).where(project.c.id == item.id)
            ).scalar_one_or_none()
            if prior_payload is not None:
                prior = _project(cast(dict[str, Any], prior_payload))
                if not _same_project_creation(prior, item):
                    raise ConflictError("project idempotency key conflicts with stored state")
                return prior
            try:
                connection.execute(
                    insert(project).values(
                        id=item.id,
                        name=item.name,
                        created_by=item.created_by,
                        created_at=item.created_at,
                        revision=item.revision,
                        idempotency_key=item.idempotency_key,
                        payload=to_json_value(item),
                        record_sha256=record_sha256(item),
                    )
                )
                connection.execute(
                    insert(project_member).values(
                        id=owner.id,
                        project_id=owner.project_id,
                        actor_id=owner.actor_id,
                        role=owner.role.value,
                        added_at=owner.added_at,
                        payload=to_json_value(owner),
                        record_sha256=record_sha256(owner),
                    )
                )
            except IntegrityError as error:
                raise ConflictError("project creator/idempotency key or owner already exists") from error
        return item

    def get_project(self, project_id: str) -> Project:
        """Return one collaborative project."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(project.c.payload).where(project.c.id == project_id)
            ).scalar_one_or_none()
        if payload is None:
            raise NotFoundError("project was not found", context={"project_id": project_id})
        return _project(cast(dict[str, Any], payload))

    def list_projects(self, actor_id: str) -> tuple[Project, ...]:
        """Return projects visible through an explicit membership row."""
        with self._engine.connect() as connection:
            payloads = connection.execute(
                select(project.c.payload)
                .join(project_member, project_member.c.project_id == project.c.id)
                .where(project_member.c.actor_id == actor_id)
                .order_by(project.c.created_at, project.c.id)
            ).scalars()
            return tuple(_project(cast(dict[str, Any], payload)) for payload in payloads)

    def get_project_bundle(self, project_id: str) -> ProjectBundle:
        """Return complete project state and immutable history."""
        with self._engine.connect() as connection:
            project_payload = connection.execute(
                select(project.c.payload).where(project.c.id == project_id)
            ).scalar_one_or_none()
            if project_payload is None:
                raise NotFoundError("project was not found", context={"project_id": project_id})

            def payloads(table: Table) -> list[dict[str, Any]]:
                """Load stable same-project JSON payloads."""
                values = connection.execute(
                    select(table.c.payload).where(table.c.project_id == project_id).order_by(table.c.id)
                ).scalars()
                return [cast(dict[str, Any], value) for value in values]

            return ProjectBundle(
                project=_project(cast(dict[str, Any], project_payload)),
                members=tuple(_project_member(item) for item in payloads(project_member)),
                search_runs=tuple(_project_search_run(item) for item in payloads(project_search_run)),
                screening_events=tuple(_screening_event(item) for item in payloads(screening_event)),
                notes=tuple(_project_note(item) for item in payloads(project_note)),
                evidence_records=tuple(_project_evidence_record(item) for item in payloads(project_evidence_record)),
                evidence_tables=tuple(_evidence_table(item) for item in payloads(evidence_table)),
                evidence_table_rows=tuple(_evidence_table_row(item) for item in payloads(evidence_table_row)),
                policy_links=tuple(_policy_link(item) for item in payloads(policy_link)),
                review_events=tuple(_review_event(item) for item in payloads(review_event)),
                exports=tuple(_project_export(item) for item in payloads(project_export)),
            )

    def put_project_member(self, item: ProjectMember, *, expected_revision: int) -> Project:
        """Add a project member with optimistic revision checking."""
        return self._put_project_item(
            project_member,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "actor_id": item.actor_id,
                "role": item.role.value,
                "added_at": item.added_at,
            },
            expected_revision,
        )

    def put_project_search_run(self, item: ProjectSearchRun, *, expected_revision: int) -> Project:
        """Attach one saved run with optimistic revision checking."""
        return self._put_project_item(
            project_search_run,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "search_run_id": item.search_run_id,
                "saved_at": item.saved_at,
            },
            expected_revision,
        )

    def put_screening_event(self, item: ScreeningEvent, *, expected_revision: int) -> Project:
        """Append a screening event."""
        return self._put_project_item(
            screening_event,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "work_id": item.work_id,
                "outcome": item.outcome.value,
                "decided_at": item.decided_at,
            },
            expected_revision,
        )

    def put_project_note(self, item: ProjectNote, *, expected_revision: int) -> Project:
        """Append a project note."""
        return self._put_project_item(
            project_note,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def put_project_evidence_record(self, item: ProjectEvidenceRecord, *, expected_revision: int) -> Project:
        """Persist a project evidence record."""
        return self._put_project_item(
            project_evidence_record,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "work_id": item.work_id,
                "document_version_id": item.document_version_id,
                "record_type": item.record_type,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def put_evidence_table(self, item: EvidenceTable, *, expected_revision: int) -> Project:
        """Persist an evidence-table schema."""
        return self._put_project_item(
            evidence_table,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "name": item.name,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def put_evidence_table_row(self, item: EvidenceTableRow, *, expected_revision: int) -> Project:
        """Persist an evidence-table row."""
        return self._put_project_item(
            evidence_table_row,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "table_id": item.table_id,
                "evidence_record_id": item.evidence_record_id,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def put_policy_link(self, item: PolicyLink, *, expected_revision: int) -> Project:
        """Persist a typed policy link."""
        return self._put_project_item(
            policy_link,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "research_work_id": item.research_work_id,
                "policy_work_id": item.policy_work_id,
                "relationship_type": item.relationship_type.value,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def put_review_event(self, item: ReviewEvent, *, expected_revision: int) -> Project:
        """Append a review transition."""
        return self._put_project_item(
            review_event,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "target_type": item.target_type.value,
                "target_id": item.target_id,
                "reviewed_at": item.reviewed_at,
            },
            expected_revision,
        )

    def get_review_event(self, event_id: str) -> ReviewEvent | None:
        """Return one immutable review event."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(review_event.c.payload).where(review_event.c.id == event_id)
            ).scalar_one_or_none()
        return None if payload is None else _review_event(cast(dict[str, Any], payload))

    def put_project_export(self, item: ProjectExport, *, expected_revision: int) -> Project:
        """Persist a checksum-addressed evidence-table export."""
        return self._put_project_item(
            project_export,
            item,
            {
                "id": item.id,
                "project_id": item.project_id,
                "table_id": item.table_id,
                "format": item.format.value,
                "created_at": item.created_at,
            },
            expected_revision,
        )

    def get_project_export(self, export_id: str) -> ProjectExport:
        """Return one immutable project export."""
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(project_export.c.payload).where(project_export.c.id == export_id)
            ).scalar_one_or_none()
        if payload is None:
            raise NotFoundError("project export was not found", context={"export_id": export_id})
        return _project_export(cast(dict[str, Any], payload))

    def put_tool_invocation(self, item: ToolInvocationRecord) -> None:
        """Append one privacy-safe tool invocation idempotently."""
        with self._engine.begin() as connection:
            _put_immutable(
                connection,
                tool_invocation,
                item.id,
                {
                    "id": item.id,
                    "actor_id": item.actor_id,
                    "tool_name": item.tool_name.value,
                    "arguments_sha256": item.arguments_sha256,
                    "outcome": item.outcome.value,
                    "occurred_at": item.occurred_at,
                    "duration_ms": item.duration_ms,
                    "payload": to_json_value(item),
                },
                item,
            )

    def list_tool_invocations(self, *, limit: int = 100) -> tuple[ToolInvocationRecord, ...]:
        """Return newest tool invocations under a hard operational bound."""
        bounded = max(0, min(limit, 1_000))
        with self._engine.connect() as connection:
            payloads = connection.execute(
                select(tool_invocation.c.payload)
                .order_by(tool_invocation.c.occurred_at.desc(), tool_invocation.c.id.desc())
                .limit(bounded)
            ).scalars()
            return tuple(_tool_invocation(cast(dict[str, Any], payload)) for payload in payloads)

    def _put_project_item(
        self,
        table: Table,
        item: object,
        values: dict[str, object],
        expected_revision: int,
    ) -> Project:
        """Append one immutable item and atomically bump its project revision."""
        project_item = cast(Any, item)
        project_id = str(project_item.project_id)
        identifier = str(project_item.id)
        digest = record_sha256(item)
        with self._engine.begin() as connection:
            prior = connection.execute(
                select(table.c.record_sha256).where(table.c.id == identifier)
            ).scalar_one_or_none()
            project_row = (
                connection.execute(select(project.c.payload, project.c.revision).where(project.c.id == project_id))
                .mappings()
                .one_or_none()
            )
            if project_row is None:
                raise NotFoundError("project was not found", context={"project_id": project_id})
            current = _project(cast(dict[str, Any], project_row["payload"]))
            if prior is not None:
                if prior != digest:
                    raise ConflictError("project operation idempotency key conflicts with stored state")
                return current
            actual_revision = int(cast(int, project_row["revision"]))
            if actual_revision != expected_revision:
                raise ConflictError(
                    "project was modified concurrently",
                    context={
                        "expected_revision": str(expected_revision),
                        "actual_revision": str(actual_revision),
                    },
                )
            try:
                connection.execute(
                    insert(table).values(
                        **values,
                        payload=to_json_value(item),
                        record_sha256=digest,
                    )
                )
            except IntegrityError as error:
                raise ConflictError("project mutation violates a uniqueness constraint") from error
            revised = replace(current, revision=current.revision + 1)
            result = connection.execute(
                update(project)
                .where(project.c.id == project_id, project.c.revision == actual_revision)
                .values(
                    revision=revised.revision,
                    payload=to_json_value(revised),
                    record_sha256=record_sha256(revised),
                )
            )
            if result.rowcount != 1:
                raise ConflictError("project was modified concurrently")
            return revised

    def _put_governance_trace(
        self,
        connection: Connection,
        decisions: tuple[PolicyDecisionRecord, ...],
        robots: tuple[RobotsObservation, ...],
    ) -> None:
        """Persist governance records within an existing transaction."""
        for item in decisions:
            _put_immutable(
                connection,
                policy_decision,
                item.id,
                {
                    "id": item.id,
                    "source_code": item.source_code,
                    "policy_version": item.policy_version,
                    "action": item.action,
                    "url": item.url,
                    "content_class": item.content_class,
                    "outcome": item.outcome.value,
                    "reason": item.reason,
                    "decided_at": item.decided_at,
                },
                item,
            )
        for item in robots:
            _put_immutable(
                connection,
                robots_observation,
                item.id,
                {
                    "id": item.id,
                    "host": item.host,
                    "robots_url": item.robots_url,
                    "target_url": item.target_url,
                    "observed_at": item.observed_at,
                    "http_status": item.http_status,
                    "content_sha256": item.content_sha256,
                    "allowed": item.allowed,
                    "reason": item.reason,
                },
                item,
            )

    def _put_evidence_record(self, connection: Connection, item: EvidenceRecord) -> None:
        """Persist a structured record, its field revisions, and exact anchors."""
        _put_immutable(
            connection,
            evidence_record,
            item.id,
            {
                "id": item.id,
                "work_id": item.work_id,
                "document_version_id": item.document_version_id,
                "record_type": item.record_type,
                "schema_version": item.schema_version,
            },
            item,
        )
        for ordinal, item_field in enumerate(item.fields):
            _put_immutable(
                connection,
                evidence_field_revision,
                item_field.id,
                {
                    "id": item_field.id,
                    "evidence_record_id": item.id,
                    "ordinal": ordinal,
                    "field_name": item_field.field_name,
                    "display_value": item_field.display_value,
                    "normalized_value": item_field.normalized_value,
                    "units": item_field.units,
                    "applicability": item_field.applicability,
                    "state": item_field.state.value,
                    "lineage_method": item_field.lineage_method,
                    "confidence": item_field.confidence,
                },
                {"evidence_record_id": item.id, "field": item_field},
            )
            for anchor in item_field.anchors:
                anchor_id = stable_id(
                    "anc",
                    item_field.id,
                    anchor.evidence_unit_id,
                    anchor.locator,
                    anchor.quote,
                )
                _put_immutable(
                    connection,
                    evidence_anchor,
                    anchor_id,
                    {
                        "id": anchor_id,
                        "evidence_field_revision_id": item_field.id,
                        "evidence_unit_id": anchor.evidence_unit_id,
                        "locator": anchor.locator,
                        "quote": anchor.quote,
                    },
                    {"field_id": item_field.id, "anchor": anchor},
                )

    def _evidence_records(
        self,
        connection: Connection,
        rows: list[RowMapping],
    ) -> tuple[EvidenceRecord, ...]:
        """Rehydrate evidence records and their nested revisions and anchors."""
        result: list[EvidenceRecord] = []
        for row in rows:
            record_id = str(row["id"])
            field_rows = list(
                connection.execute(
                    select(evidence_field_revision)
                    .where(evidence_field_revision.c.evidence_record_id == record_id)
                    .order_by(evidence_field_revision.c.ordinal)
                ).mappings()
            )
            domain_fields: list[EvidenceFieldRevision] = []
            for field_row in field_rows:
                field_id = str(field_row["id"])
                anchor_rows = _rows(
                    connection,
                    select(evidence_anchor).where(evidence_anchor.c.evidence_field_revision_id == field_id),
                )
                domain_fields.append(_evidence_field(field_row, anchor_rows))
            result.append(
                EvidenceRecord(
                    id=record_id,
                    work_id=str(row["work_id"]),
                    document_version_id=str(row["document_version_id"]),
                    record_type=str(row["record_type"]),
                    schema_version=str(row["schema_version"]),
                    fields=tuple(domain_fields),
                )
            )
        return tuple(result)


def _put_immutable(
    connection: Connection,
    table: Table,
    entity_id: str,
    values: dict[str, object],
    digest_source: object,
) -> None:
    """Insert an immutable row or prove an identical row already exists."""
    digest = record_sha256(digest_source)
    prior = connection.execute(select(table.c.record_sha256).where(table.c.id == entity_id)).scalar_one_or_none()
    if prior is not None:
        if prior != digest:
            raise ConflictError(
                "stored immutable record differs from supplied state",
                context={"table": table.name, "entity_id": entity_id},
            )
        return
    connection.execute(insert(table).values(**values, record_sha256=digest))


def _rows(connection: Connection, statement: Any) -> list[RowMapping]:
    """Execute a select and return stable identifier order."""
    rows = list(connection.execute(statement).mappings())
    return sorted(rows, key=lambda row: str(row["id"]))


def _rows_in(connection: Connection, table: Table, column: Any, values: Iterable[str]) -> list[RowMapping]:
    """Select rows whose supplied column occurs in a possibly empty collection."""
    unique = sorted(set(values))
    if not unique:
        return []
    return _rows(connection, select(table).where(column.in_(unique)))


def _aware(value: datetime | None) -> datetime | None:
    """Normalize SQLite's naive timestamp round trips to UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _required_aware(value: object) -> datetime:
    """Normalize a required database timestamp to UTC."""
    result = _aware(cast(datetime, value))
    if result is None:  # pragma: no cover - protected by NOT NULL constraints
        raise ConflictError("required database timestamp is null")
    return result


def _source(row: RowMapping) -> Source:
    return Source(
        id=str(row["id"]),
        code=str(row["code"]),
        display_name=str(row["display_name"]),
        source_kind=str(row["source_kind"]),
    )


def _source_policy(row: RowMapping) -> SourcePolicyVersion:
    return SourcePolicyVersion(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        version=int(cast(int, row["version"])),
        effective_at=_required_aware(row["effective_at"]),
        review_status=str(row["review_status"]),
    )


def _retrieval(row: RowMapping) -> RetrievalEvent:
    return RetrievalEvent(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        source_policy_version_id=str(row["source_policy_version_id"]),
        requested_url=str(row["requested_url"]),
        resolved_url=str(row["resolved_url"]),
        retrieved_at=_required_aware(row["retrieved_at"]),
        http_status=int(cast(int, row["http_status"])),
        media_type=str(row["media_type"]),
        content_sha256=str(row["content_sha256"]),
        collector_version=str(row["collector_version"]),
    )


def _raw_asset(row: RowMapping) -> RawAsset:
    return RawAsset(
        id=str(row["id"]),
        retrieval_event_id=str(row["retrieval_event_id"]),
        sha256=str(row["sha256"]),
        media_type=str(row["media_type"]),
        byte_length=int(cast(int, row["byte_length"])),
        storage_uri=cast(str | None, row["storage_uri"]),
        access_status=AccessStatus(str(row["access_status"])),
    )


def _external_identifier(row: RowMapping) -> ExternalIdentifier:
    return ExternalIdentifier(id=str(row["id"]), scheme=str(row["scheme"]), value=str(row["value"]))


def _document_version(row: RowMapping) -> DocumentVersion:
    return DocumentVersion(
        id=str(row["id"]),
        work_id=str(row["work_id"]),
        raw_asset_id=str(row["raw_asset_id"]),
        version_label=str(row["version_label"]),
        asserted_at=cast(Any, row["asserted_at"]),
        first_observed_at=_required_aware(row["first_observed_at"]),
        eligible_from=_aware(cast(datetime | None, row["eligible_from"])),
        temporal_safety_class=TemporalSafetyClass(str(row["temporal_safety_class"])),
    )


def _manifestation(row: RowMapping) -> Manifestation:
    return Manifestation(
        id=str(row["id"]),
        document_version_id=str(row["document_version_id"]),
        source_id=str(row["source_id"]),
        source_identifier=str(row["source_identifier"]),
        source_url=str(row["source_url"]),
        access_status=AccessStatus(str(row["access_status"])),
        license_uri=cast(str | None, row["license_uri"]),
    )


def _extraction(row: RowMapping) -> ExtractionArtifact:
    return ExtractionArtifact(
        id=str(row["id"]),
        document_version_id=str(row["document_version_id"]),
        raw_asset_id=str(row["raw_asset_id"]),
        parser_name=str(row["parser_name"]),
        parser_version=str(row["parser_version"]),
        schema_version=str(row["schema_version"]),
        text_sha256=str(row["text_sha256"]),
        created_at=_required_aware(row["created_at"]),
        temporal_safety_class=TemporalSafetyClass(str(row["temporal_safety_class"])),
    )


def _evidence_unit(row: RowMapping) -> EvidenceUnit:
    return EvidenceUnit(
        id=str(row["id"]),
        extraction_artifact_id=str(row["extraction_artifact_id"]),
        ordinal=int(cast(int, row["ordinal"])),
        section_path=tuple(cast(list[str], row["section_path"])),
        page_start=int(cast(int, row["page_start"])),
        page_end=int(cast(int, row["page_end"])),
        text=str(row["text"]),
        temporal_safety_class=TemporalSafetyClass(str(row["temporal_safety_class"])),
    )


def _evidence_field(row: RowMapping, anchors: list[RowMapping]) -> EvidenceFieldRevision:
    return EvidenceFieldRevision(
        id=str(row["id"]),
        field_name=str(row["field_name"]),
        display_value=str(row["display_value"]),
        normalized_value=cast(ScalarValue, row["normalized_value"]),
        units=cast(str | None, row["units"]),
        applicability=str(row["applicability"]),
        state=EvidenceState(str(row["state"])),
        lineage_method=str(row["lineage_method"]),
        confidence=float(cast(float, row["confidence"])),
        anchors=tuple(
            EvidenceAnchor(
                evidence_unit_id=str(anchor["evidence_unit_id"]),
                locator=str(anchor["locator"]),
                quote=str(anchor["quote"]),
            )
            for anchor in anchors
        ),
    )


def _snapshot(row: RowMapping, members: list[RowMapping]) -> CorpusSnapshot:
    return CorpusSnapshot(
        id=str(row["id"]),
        schema_version=str(row["schema_version"]),
        created_at=_required_aware(row["created_at"]),
        source_policy_version_ids=tuple(cast(list[str], row["source_policy_version_ids"])),
        members=tuple(
            SnapshotMember(
                work_id=str(member["work_id"]),
                document_version_id=str(member["document_version_id"]),
                extraction_artifact_id=str(member["extraction_artifact_id"]),
            )
            for member in members
        ),
        manifest_sha256=str(row["manifest_sha256"]),
    )


def _job_values(item: Job) -> dict[str, object]:
    """Map a job aggregate to mutable SQL columns."""
    return {
        "id": item.id,
        "kind": item.kind,
        "state": item.state.value,
        "idempotency_key": item.idempotency_key,
        "input": cast(JsonValue, to_json_value(item.input)),
        "created_at": item.created_at,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "output": None if item.output is None else cast(JsonValue, to_json_value(item.output)),
        "error_code": item.error_code,
        "error_message": item.error_message,
    }


def _job(row: RowMapping) -> Job:
    input_value = cast(dict[str, ScalarValue], row["input"])
    output_value = cast(dict[str, ScalarValue] | None, row["output"])
    return Job(
        id=str(row["id"]),
        kind=str(row["kind"]),
        state=JobState(str(row["state"])),
        idempotency_key=str(row["idempotency_key"]),
        input=MappingProxyType(dict(input_value)),
        created_at=_required_aware(row["created_at"]),
        started_at=_aware(cast(datetime | None, row["started_at"])),
        finished_at=_aware(cast(datetime | None, row["finished_at"])),
        output=None if output_value is None else MappingProxyType(dict(output_value)),
        error_code=cast(str | None, row["error_code"]),
        error_message=cast(str | None, row["error_message"]),
    )


def _source_metadata(payload: dict[str, Any]) -> SourceMetadataRecord:
    """Rehydrate source metadata from its strict JSON payload."""
    identifiers = tuple(
        IdentifierValue(scheme=str(item["scheme"]), value=str(item["value"]))
        for item in cast(list[dict[str, Any]], payload["identifiers"])
    )
    links = tuple(
        ContentLink(
            url=str(item["url"]),
            media_type=cast(str | None, item["media_type"]),
            content_class=str(item["content_class"]),
            license_uri=cast(str | None, item["license_uri"]),
        )
        for item in cast(list[dict[str, Any]], payload["content_links"])
    )
    published = cast(str | None, payload["published_at"])
    updated = cast(str | None, payload["asserted_updated_at"])
    return SourceMetadataRecord(
        id=str(payload["id"]),
        source_code=str(payload["source_code"]),
        source_identifier=str(payload["source_identifier"]),
        metadata_url=str(payload["metadata_url"]),
        title=str(payload["title"]),
        authors=tuple(cast(list[str], payload["authors"])),
        abstract=cast(str | None, payload["abstract"]),
        published_at=None if published is None else date.fromisoformat(published),
        asserted_updated_at=None if updated is None else _json_datetime(updated),
        identifiers=identifiers,
        content_links=links,
        observed_at=_json_datetime(str(payload["observed_at"])),
        metadata_sha256=str(payload["metadata_sha256"]),
        metadata_storage_uri=str(payload["metadata_storage_uri"]),
    )


def _acquired_asset(payload: dict[str, Any]) -> AcquiredAsset:
    """Rehydrate an acquired asset from its strict JSON payload."""
    return AcquiredAsset(
        id=str(payload["id"]),
        source_record_id=str(payload["source_record_id"]),
        requested_url=str(payload["requested_url"]),
        resolved_url=str(payload["resolved_url"]),
        retrieved_at=_json_datetime(str(payload["retrieved_at"])),
        http_status=int(cast(int, payload["http_status"])),
        media_type=str(payload["media_type"]),
        sha256=str(payload["sha256"]),
        byte_length=int(cast(int, payload["byte_length"])),
        storage_uri=str(payload["storage_uri"]),
        rights_status=str(payload["rights_status"]),
        license_uri=cast(str | None, payload["license_uri"]),
    )


def _normalized_document(payload: dict[str, Any]) -> NormalizedDocument:
    """Rehydrate a normalized document and structural units."""
    quality_payload = cast(dict[str, Any], payload["quality"])
    units = tuple(
        NormalizedUnit(
            id=str(item["id"]),
            document_id=str(item["document_id"]),
            ordinal=int(cast(int, item["ordinal"])),
            section_path=tuple(cast(list[str], item["section_path"])),
            locator=str(item["locator"]),
            page_start=cast(int | None, item["page_start"]),
            page_end=cast(int | None, item["page_end"]),
            text=str(item["text"]),
        )
        for item in cast(list[dict[str, Any]], payload["units"])
    )
    return NormalizedDocument(
        id=str(payload["id"]),
        acquired_asset_id=str(payload["acquired_asset_id"]),
        parser_name=str(payload["parser_name"]),
        parser_version=str(payload["parser_version"]),
        schema_version=str(payload["schema_version"]),
        text_sha256=str(payload["text_sha256"]),
        text_storage_uri=str(payload["text_storage_uri"]),
        created_at=_json_datetime(str(payload["created_at"])),
        units=units,
        quality=QualityReport(
            parser_route=str(quality_payload["parser_route"]),
            page_count=cast(int | None, quality_payload["page_count"]),
            character_count=int(cast(int, quality_payload["character_count"])),
            text_density=cast(float | None, quality_payload["text_density"]),
            ocr_required=bool(quality_payload["ocr_required"]),
            warnings=tuple(cast(list[str], quality_payload["warnings"])),
        ),
    )


def _index_generation(payload: dict[str, Any]) -> IndexGeneration:
    """Rehydrate an immutable index generation."""
    return IndexGeneration(
        id=str(payload["id"]),
        mapping_version=str(payload["mapping_version"]),
        retrieval_config_id=str(payload["retrieval_config_id"]),
        embedding_config_id=str(payload["embedding_config_id"]),
        corpus_snapshot_id=str(payload["corpus_snapshot_id"]),
        manifest_sha256=str(payload["manifest_sha256"]),
        work_index_name=str(payload["work_index_name"]),
        evidence_index_name=str(payload["evidence_index_name"]),
        work_document_count=int(cast(int, payload["work_document_count"])),
        evidence_document_count=int(cast(int, payload["evidence_document_count"])),
        created_at=_json_datetime(str(payload["created_at"])),
        activated_at=_json_datetime(str(payload["activated_at"])),
    )


def _search_run(payload: dict[str, Any]) -> SearchRun:
    """Rehydrate an immutable saved search run."""
    return SearchRun(
        id=str(payload["id"]),
        request_payload=cast(dict[str, object], payload["request_payload"]),
        response_payload=cast(dict[str, object], payload["response_payload"]),
        index_generation_id=str(payload["index_generation_id"]),
        query_sha256=str(payload["query_sha256"]),
        created_at=_json_datetime(str(payload["created_at"])),
    )


def _project(payload: dict[str, Any]) -> Project:
    """Rehydrate a collaborative project."""
    return Project(
        id=str(payload["id"]),
        name=str(payload["name"]),
        description=str(payload["description"]),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        baseline_corpus_snapshot_id=cast(str | None, payload["baseline_corpus_snapshot_id"]),
        baseline_index_generation_id=cast(str | None, payload["baseline_index_generation_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        revision=int(cast(int, payload["revision"])),
    )


def _same_project_creation(left: Project, right: Project) -> bool:
    """Compare immutable create inputs while ignoring server time and later revision."""
    return (
        left.id,
        left.name,
        left.description,
        left.created_by,
        left.baseline_corpus_snapshot_id,
        left.baseline_index_generation_id,
        left.idempotency_key,
    ) == (
        right.id,
        right.name,
        right.description,
        right.created_by,
        right.baseline_corpus_snapshot_id,
        right.baseline_index_generation_id,
        right.idempotency_key,
    )


def _project_member(payload: dict[str, Any]) -> ProjectMember:
    """Rehydrate one project membership."""
    return ProjectMember(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        actor_id=str(payload["actor_id"]),
        role=ProjectRole(str(payload["role"])),
        added_by=str(payload["added_by"]),
        added_at=_json_datetime(str(payload["added_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _project_search_run(payload: dict[str, Any]) -> ProjectSearchRun:
    """Rehydrate one attached saved search."""
    return ProjectSearchRun(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        search_run_id=str(payload["search_run_id"]),
        saved_by=str(payload["saved_by"]),
        saved_at=_json_datetime(str(payload["saved_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _screening_event(payload: dict[str, Any]) -> ScreeningEvent:
    """Rehydrate one screening decision."""
    return ScreeningEvent(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        work_id=str(payload["work_id"]),
        document_version_id=cast(str | None, payload["document_version_id"]),
        outcome=ScreeningOutcome(str(payload["outcome"])),
        reason_code=cast(str | None, payload["reason_code"]),
        note=cast(str | None, payload["note"]),
        decided_by=str(payload["decided_by"]),
        decided_at=_json_datetime(str(payload["decided_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _project_note(payload: dict[str, Any]) -> ProjectNote:
    """Rehydrate one project note."""
    return ProjectNote(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        target_type=str(payload["target_type"]),
        target_id=str(payload["target_id"]),
        body=str(payload["body"]),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _project_anchor(payload: dict[str, Any]) -> ProjectEvidenceAnchor:
    """Rehydrate one policy-checked evidence anchor."""
    return ProjectEvidenceAnchor(
        evidence_unit_id=str(payload["evidence_unit_id"]),
        locator=str(payload["locator"]),
        quote=str(payload["quote"]),
        content_sha256=str(payload["content_sha256"]),
    )


def _project_evidence_field(payload: dict[str, Any]) -> ProjectEvidenceField:
    """Rehydrate one project evidence field."""
    return ProjectEvidenceField(
        id=str(payload["id"]),
        field_name=str(payload["field_name"]),
        display_value=str(payload["display_value"]),
        normalized_value=cast(ScalarValue, payload["normalized_value"]),
        units=cast(str | None, payload["units"]),
        applicability=str(payload["applicability"]),
        missingness_reason=cast(str | None, payload["missingness_reason"]),
        state=EvidenceState(str(payload["state"])),
        lineage_method=EvidenceLineageMethod(str(payload["lineage_method"])),
        confidence=float(cast(float, payload["confidence"])),
        anchors=tuple(_project_anchor(item) for item in cast(list[dict[str, Any]], payload["anchors"])),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
    )


def _project_evidence_record(payload: dict[str, Any]) -> ProjectEvidenceRecord:
    """Rehydrate one anchored project evidence record."""
    return ProjectEvidenceRecord(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        work_id=str(payload["work_id"]),
        document_version_id=str(payload["document_version_id"]),
        record_type=str(payload["record_type"]),
        schema_version=str(payload["schema_version"]),
        fields=tuple(_project_evidence_field(item) for item in cast(list[dict[str, Any]], payload["fields"])),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _evidence_table(payload: dict[str, Any]) -> EvidenceTable:
    """Rehydrate one evidence-table schema."""
    return EvidenceTable(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        name=str(payload["name"]),
        description=str(payload["description"]),
        columns=tuple(
            EvidenceTableColumn(
                key=str(item["key"]),
                label=str(item["label"]),
                value_type=str(item["value_type"]),
                units=cast(str | None, item["units"]),
            )
            for item in cast(list[dict[str, Any]], payload["columns"])
        ),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _evidence_table_row(payload: dict[str, Any]) -> EvidenceTableRow:
    """Rehydrate one evidence-table row."""
    return EvidenceTableRow(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        table_id=str(payload["table_id"]),
        evidence_record_id=str(payload["evidence_record_id"]),
        label=str(payload["label"]),
        cells=tuple(
            EvidenceTableCell(
                column_key=str(item["column_key"]),
                evidence_field_id=cast(str | None, item["evidence_field_id"]),
                missingness_reason=cast(str | None, item["missingness_reason"]),
            )
            for item in cast(list[dict[str, Any]], payload["cells"])
        ),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _policy_link(payload: dict[str, Any]) -> PolicyLink:
    """Rehydrate one typed policy link."""
    anchor = cast(dict[str, Any] | None, payload["evidence_anchor"])
    return PolicyLink(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        research_work_id=str(payload["research_work_id"]),
        policy_work_id=str(payload["policy_work_id"]),
        relationship_type=PolicyLinkType(str(payload["relationship_type"])),
        evidence_anchor=None if anchor is None else _project_anchor(anchor),
        method=str(payload["method"]),
        confidence=float(cast(float, payload["confidence"])),
        state=EvidenceState(str(payload["state"])),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _review_event(payload: dict[str, Any]) -> ReviewEvent:
    """Rehydrate one append-only review transition."""
    return ReviewEvent(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        target_type=ReviewTargetType(str(payload["target_type"])),
        target_id=str(payload["target_id"]),
        previous_state=EvidenceState(str(payload["previous_state"])),
        new_state=EvidenceState(str(payload["new_state"])),
        reason=str(payload["reason"]),
        reviewed_by=str(payload["reviewed_by"]),
        reviewed_at=_json_datetime(str(payload["reviewed_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _project_export(payload: dict[str, Any]) -> ProjectExport:
    """Rehydrate one project evidence-table export."""
    return ProjectExport(
        id=str(payload["id"]),
        project_id=str(payload["project_id"]),
        table_id=str(payload["table_id"]),
        format=ProjectExportFormat(str(payload["format"])),
        project_revision=int(cast(int, payload["project_revision"])),
        data_uri=str(payload["data_uri"]),
        data_sha256=str(payload["data_sha256"]),
        data_media_type=str(payload["data_media_type"]),
        data_byte_length=int(cast(int, payload["data_byte_length"])),
        manifest_uri=str(payload["manifest_uri"]),
        manifest_sha256=str(payload["manifest_sha256"]),
        manifest_byte_length=int(cast(int, payload["manifest_byte_length"])),
        warnings=tuple(cast(list[str], payload["warnings"])),
        created_by=str(payload["created_by"]),
        created_at=_json_datetime(str(payload["created_at"])),
        idempotency_key=str(payload["idempotency_key"]),
    )


def _tool_invocation(payload: dict[str, Any]) -> ToolInvocationRecord:
    """Rehydrate one privacy-safe tool invocation audit record."""
    return ToolInvocationRecord(
        id=str(payload["id"]),
        actor_id=str(payload["actor_id"]),
        tool_name=AgentToolName(str(payload["tool_name"])),
        arguments_sha256=str(payload["arguments_sha256"]),
        outcome=ToolInvocationOutcome(str(payload["outcome"])),
        result_sha256=cast(str | None, payload["result_sha256"]),
        target_ids=tuple(cast(list[str], payload["target_ids"])),
        duration_ms=int(cast(int, payload["duration_ms"])),
        occurred_at=_json_datetime(str(payload["occurred_at"])),
    )


def _json_datetime(value: str) -> datetime:
    """Parse strict JSON UTC timestamps."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
