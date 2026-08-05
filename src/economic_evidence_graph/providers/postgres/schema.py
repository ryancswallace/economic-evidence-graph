"""SQLAlchemy Core schema shared by the PostgreSQL adapter and Alembic."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

_naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=_naming_convention)

source = Table(
    "source",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("source_kind", String(64), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

source_policy_version = Table(
    "source_policy_version",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_id", String(64), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=False),
    Column("review_status", String(64), nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("version > 0", name="positive_version"),
    UniqueConstraint("source_id", "version", name="uq_source_policy_version_source_version"),
)

retrieval_event = Table(
    "retrieval_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_id", String(64), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False),
    Column(
        "source_policy_version_id",
        String(64),
        ForeignKey("source_policy_version.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("requested_url", Text, nullable=False),
    Column("resolved_url", Text, nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("http_status", Integer, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("collector_version", String(128), nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("http_status >= 100 AND http_status <= 599", name="valid_http_status"),
)
Index("ix_retrieval_event_source_retrieved", retrieval_event.c.source_id, retrieval_event.c.retrieved_at)

raw_asset = Table(
    "raw_asset",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("retrieval_event_id", String(64), ForeignKey("retrieval_event.id", ondelete="RESTRICT"), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("storage_uri", Text),
    Column("access_status", String(32), nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("byte_length >= 0", name="nonnegative_byte_length"),
)
Index("ix_raw_asset_sha256", raw_asset.c.sha256)

work = Table(
    "work",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("title", Text, nullable=False),
    Column("work_type", String(32), nullable=False),
    Column("authors", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

external_identifier = Table(
    "external_identifier",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("work_id", String(64), ForeignKey("work.id", ondelete="CASCADE"), nullable=False),
    Column("scheme", String(64), nullable=False),
    Column("value", Text, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("scheme", "value", name="uq_external_identifier_scheme_value"),
)

document_version = Table(
    "document_version",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("work_id", String(64), ForeignKey("work.id", ondelete="RESTRICT"), nullable=False),
    Column("raw_asset_id", String(64), ForeignKey("raw_asset.id", ondelete="RESTRICT"), nullable=False),
    Column("version_label", Text, nullable=False),
    Column("asserted_at", Date),
    Column("first_observed_at", DateTime(timezone=True), nullable=False),
    Column("eligible_from", DateTime(timezone=True)),
    Column("temporal_safety_class", String(2), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_document_version_work_observed", document_version.c.work_id, document_version.c.first_observed_at)

manifestation = Table(
    "manifestation",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("document_version_id", String(64), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=False),
    Column("source_id", String(64), ForeignKey("source.id", ondelete="RESTRICT"), nullable=False),
    Column("source_identifier", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("access_status", String(32), nullable=False),
    Column("license_uri", Text),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("source_id", "source_identifier", "source_url", name="uq_manifestation_source_locator"),
)

extraction_artifact = Table(
    "extraction_artifact",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("document_version_id", String(64), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=False),
    Column("raw_asset_id", String(64), ForeignKey("raw_asset.id", ondelete="RESTRICT"), nullable=False),
    Column("parser_name", String(64), nullable=False),
    Column("parser_version", String(128), nullable=False),
    Column("schema_version", String(128), nullable=False),
    Column("text_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("temporal_safety_class", String(2), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

evidence_unit = Table(
    "evidence_unit",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "extraction_artifact_id",
        String(64),
        ForeignKey("extraction_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("section_path", JSON, nullable=False),
    Column("page_start", Integer, nullable=False),
    Column("page_end", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("temporal_safety_class", String(2), nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("ordinal >= 0", name="nonnegative_ordinal"),
    CheckConstraint("page_start >= 1 AND page_end >= page_start", name="valid_page_interval"),
    UniqueConstraint("extraction_artifact_id", "ordinal", name="uq_evidence_unit_artifact_ordinal"),
)

evidence_record = Table(
    "evidence_record",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("work_id", String(64), ForeignKey("work.id", ondelete="RESTRICT"), nullable=False),
    Column("document_version_id", String(64), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=False),
    Column("record_type", String(64), nullable=False),
    Column("schema_version", String(128), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

evidence_field_revision = Table(
    "evidence_field_revision",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("evidence_record_id", String(64), ForeignKey("evidence_record.id", ondelete="CASCADE"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("field_name", String(64), nullable=False),
    Column("display_value", Text, nullable=False),
    Column("normalized_value", JSON),
    Column("units", String(128)),
    Column("applicability", Text, nullable=False),
    Column("state", String(32), nullable=False),
    Column("lineage_method", String(64), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="valid_confidence"),
    CheckConstraint("ordinal >= 0", name="nonnegative_ordinal"),
    UniqueConstraint("evidence_record_id", "ordinal", name="uq_evidence_field_record_ordinal"),
)
Index(
    "ix_evidence_field_record_name",
    evidence_field_revision.c.evidence_record_id,
    evidence_field_revision.c.field_name,
)

evidence_anchor = Table(
    "evidence_anchor",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "evidence_field_revision_id",
        String(64),
        ForeignKey("evidence_field_revision.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("evidence_unit_id", String(64), ForeignKey("evidence_unit.id", ondelete="RESTRICT"), nullable=False),
    Column("locator", Text, nullable=False),
    Column("quote", Text, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

corpus_snapshot = Table(
    "corpus_snapshot",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("schema_version", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("source_policy_version_ids", JSON, nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("sealed", Boolean, nullable=False, default=True),
    Column("record_sha256", String(64), nullable=False),
)

snapshot_member = Table(
    "snapshot_member",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("snapshot_id", String(64), ForeignKey("corpus_snapshot.id", ondelete="CASCADE"), nullable=False),
    Column("work_id", String(64), ForeignKey("work.id", ondelete="RESTRICT"), nullable=False),
    Column("document_version_id", String(64), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=False),
    Column(
        "extraction_artifact_id",
        String(64),
        ForeignKey("extraction_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("snapshot_id", "work_id", name="uq_snapshot_member_snapshot_work"),
)

job = Table(
    "job",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("idempotency_key", String(255), nullable=False, unique=True),
    Column("input", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("output", JSON),
    Column("error_code", String(128)),
    Column("error_message", Text),
    Column("revision", Integer, nullable=False, default=1),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("revision > 0", name="positive_revision"),
)
Index("ix_job_state_created", job.c.state, job.c.created_at)

outbox_event = Table(
    "outbox_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", String(64), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
)
Index("ix_outbox_event_unpublished", outbox_event.c.published_at, outbox_event.c.created_at)

policy_decision = Table(
    "policy_decision",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_code", String(64), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("action", String(64), nullable=False),
    Column("url", Text, nullable=False),
    Column("content_class", String(128), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("reason", String(128), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_policy_decision_source_action", policy_decision.c.source_code, policy_decision.c.action)

robots_observation = Table(
    "robots_observation",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("host", String(255), nullable=False),
    Column("robots_url", Text, nullable=False),
    Column("target_url", Text, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("http_status", Integer, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("allowed", Boolean, nullable=False),
    Column("reason", String(128), nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_robots_observation_host_observed", robots_observation.c.host, robots_observation.c.observed_at)

source_metadata_record = Table(
    "source_metadata_record",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_code", String(64), nullable=False),
    Column("source_identifier", Text, nullable=False),
    Column("metadata_sha256", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint(
        "source_code",
        "source_identifier",
        "metadata_sha256",
        "observed_at",
        name="uq_source_metadata_observation",
    ),
)
Index(
    "ix_source_metadata_source_identifier_observed",
    source_metadata_record.c.source_code,
    source_metadata_record.c.source_identifier,
    source_metadata_record.c.observed_at,
)

acquired_asset = Table(
    "acquired_asset",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "source_record_id",
        String(64),
        ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sha256", String(64), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_acquired_asset_sha256", acquired_asset.c.sha256)

acquisition_failure = Table(
    "acquisition_failure",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "source_record_id",
        String(64),
        ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("requested_url", Text, nullable=False),
    Column("failure_code", String(128), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_acquisition_failure_code_occurred", acquisition_failure.c.failure_code, acquisition_failure.c.occurred_at)

normalized_document = Table(
    "normalized_document",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("acquired_asset_id", String(64), ForeignKey("acquired_asset.id", ondelete="RESTRICT"), nullable=False),
    Column("text_sha256", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

normalized_unit = Table(
    "normalized_unit",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "normalized_document_id",
        String(64),
        ForeignKey("normalized_document.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("normalized_document_id", "ordinal", name="uq_normalized_unit_document_ordinal"),
)

quarantine_record = Table(
    "quarantine_record",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("acquired_asset_id", String(64), ForeignKey("acquired_asset.id", ondelete="RESTRICT"), nullable=False),
    Column("reason", String(64), nullable=False),
    Column("retryable", Boolean, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_quarantine_record_reason", quarantine_record.c.reason)

identity_candidate = Table(
    "identity_candidate",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "left_source_record_id",
        String(64),
        ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "right_source_record_id",
        String(64),
        ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("method", String(64), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("hard_conflict", Boolean, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_identity_candidate_method_confidence", identity_candidate.c.method, identity_candidate.c.confidence)

source_change_event = Table(
    "source_change_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_code", String(64), nullable=False),
    Column("source_identifier", Text, nullable=False),
    Column(
        "current_record_id",
        String(64),
        ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("kind", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_source_change_event_source_kind", source_change_event.c.source_code, source_change_event.c.kind)

suppression_record = Table(
    "suppression_record",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("scope_type", String(32), nullable=False),
    Column("scope_value", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("physical_deletion_requested", Boolean, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("scope_type", "scope_value", name="uq_suppression_scope_value"),
)
Index("ix_suppression_scope", suppression_record.c.scope_type, suppression_record.c.scope_value)

index_generation = Table(
    "index_generation",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("mapping_version", String(128), nullable=False),
    Column("retrieval_config_id", String(128), nullable=False),
    Column("embedding_config_id", String(128), nullable=False),
    Column("corpus_snapshot_id", String(64), nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("work_index_name", String(255), nullable=False, unique=True),
    Column("evidence_index_name", String(255), nullable=False, unique=True),
    Column("work_document_count", Integer, nullable=False),
    Column("evidence_document_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("work_document_count >= 0", name="nonnegative_work_document_count"),
    CheckConstraint("evidence_document_count >= 0", name="nonnegative_evidence_document_count"),
)
Index("ix_index_generation_activated", index_generation.c.activated_at)

search_run = Table(
    "search_run",
    metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "index_generation_id",
        String(64),
        ForeignKey("index_generation.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("query_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_search_run_generation_created", search_run.c.index_generation_id, search_run.c.created_at)

project = Table(
    "project",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("created_by", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("revision > 0", name="positive_revision"),
    UniqueConstraint("created_by", "idempotency_key", name="uq_project_creator_operation"),
)
Index("ix_project_created", project.c.created_at)

project_member = Table(
    "project_member",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("role", String(32), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("project_id", "actor_id", name="uq_project_member_actor"),
)
Index("ix_project_member_actor", project_member.c.actor_id, project_member.c.project_id)

project_search_run = Table(
    "project_search_run",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("search_run_id", String(64), ForeignKey("search_run.id", ondelete="RESTRICT"), nullable=False),
    Column("saved_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("project_id", "search_run_id", name="uq_project_search_run_attachment"),
)

screening_event = Table(
    "screening_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("work_id", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index(
    "ix_screening_project_work",
    screening_event.c.project_id,
    screening_event.c.work_id,
    screening_event.c.decided_at,
)

project_note = Table(
    "project_note",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("target_type", String(64), nullable=False),
    Column("target_id", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_project_note_target", project_note.c.project_id, project_note.c.target_type, project_note.c.target_id)

project_evidence_record = Table(
    "project_evidence_record",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("work_id", String(64), nullable=False),
    Column("document_version_id", String(64), nullable=False),
    Column("record_type", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_project_evidence_work", project_evidence_record.c.project_id, project_evidence_record.c.work_id)

evidence_table = Table(
    "evidence_table",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)

evidence_table_row = Table(
    "evidence_table_row",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("table_id", String(64), ForeignKey("evidence_table.id", ondelete="CASCADE"), nullable=False),
    Column(
        "evidence_record_id",
        String(64),
        ForeignKey("project_evidence_record.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    UniqueConstraint("table_id", "evidence_record_id", name="uq_evidence_table_record_row"),
)

policy_link = Table(
    "policy_link",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("research_work_id", String(64), nullable=False),
    Column("policy_work_id", String(64), nullable=False),
    Column("relationship_type", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_policy_link_research", policy_link.c.project_id, policy_link.c.research_work_id)
Index("ix_policy_link_policy", policy_link.c.project_id, policy_link.c.policy_work_id)

review_event = Table(
    "review_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("target_id", String(64), nullable=False),
    Column("reviewed_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_review_event_target", review_event.c.project_id, review_event.c.target_type, review_event.c.target_id)

project_export = Table(
    "project_export",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
    Column("table_id", String(64), ForeignKey("evidence_table.id", ondelete="RESTRICT"), nullable=False),
    Column("format", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
)
Index("ix_project_export_created", project_export.c.project_id, project_export.c.created_at)

tool_invocation = Table(
    "tool_invocation",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("actor_id", String(255), nullable=False),
    Column("tool_name", String(64), nullable=False),
    Column("arguments_sha256", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("record_sha256", String(64), nullable=False),
    CheckConstraint("duration_ms >= 0", name="nonnegative_duration_ms"),
)
Index("ix_tool_invocation_actor_time", tool_invocation.c.actor_id, tool_invocation.c.occurred_at)
Index("ix_tool_invocation_tool_outcome", tool_invocation.c.tool_name, tool_invocation.c.outcome)
