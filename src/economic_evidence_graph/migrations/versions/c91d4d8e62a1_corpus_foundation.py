"""corpus foundation

Revision ID: c91d4d8e62a1
Revises: ab46c74c5f2e
Create Date: 2026-08-04 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c91d4d8e62a1"  # pragma: allowlist secret
down_revision: str | None = "ab46c74c5f2e"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the governed corpus schema."""
    op.create_table(
        "policy_decision",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_class", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_policy_decision_source_action", "policy_decision", ["source_code", "action"])
    op.create_table(
        "robots_observation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("robots_url", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_robots_observation_host_observed", "robots_observation", ["host", "observed_at"])
    op.create_table(
        "source_metadata_record",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("source_identifier", sa.Text(), nullable=False),
        sa.Column("metadata_sha256", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "source_code",
            "source_identifier",
            "metadata_sha256",
            "observed_at",
            name="uq_source_metadata_observation",
        ),
    )
    op.create_index(
        "ix_source_metadata_source_identifier_observed",
        "source_metadata_record",
        ["source_code", "source_identifier", "observed_at"],
    )
    op.create_table(
        "acquired_asset",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "source_record_id",
            sa.String(64),
            sa.ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_acquired_asset_sha256", "acquired_asset", ["sha256"])
    op.create_table(
        "normalized_document",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "acquired_asset_id",
            sa.String(64),
            sa.ForeignKey("acquired_asset.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_table(
        "normalized_unit",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "normalized_document_id",
            sa.String(64),
            sa.ForeignKey("normalized_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("normalized_document_id", "ordinal", name="uq_normalized_unit_document_ordinal"),
    )
    op.create_table(
        "quarantine_record",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "acquired_asset_id",
            sa.String(64),
            sa.ForeignKey("acquired_asset.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_quarantine_record_reason", "quarantine_record", ["reason"])
    op.create_table(
        "identity_candidate",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "left_source_record_id",
            sa.String(64),
            sa.ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "right_source_record_id",
            sa.String(64),
            sa.ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("hard_conflict", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_identity_candidate_method_confidence",
        "identity_candidate",
        ["method", "confidence"],
    )
    op.create_table(
        "source_change_event",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("source_identifier", sa.Text(), nullable=False),
        sa.Column(
            "current_record_id",
            sa.String(64),
            sa.ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_source_change_event_source_kind",
        "source_change_event",
        ["source_code", "kind"],
    )
    op.create_table(
        "suppression_record",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_value", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("physical_deletion_requested", sa.Boolean(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_value", name="uq_suppression_scope_value"),
    )
    op.create_index("ix_suppression_scope", "suppression_record", ["scope_type", "scope_value"])


def downgrade() -> None:
    """Remove the governed corpus schema."""
    op.drop_index("ix_suppression_scope", table_name="suppression_record")
    op.drop_table("suppression_record")
    op.drop_index("ix_source_change_event_source_kind", table_name="source_change_event")
    op.drop_table("source_change_event")
    op.drop_index("ix_identity_candidate_method_confidence", table_name="identity_candidate")
    op.drop_table("identity_candidate")
    op.drop_index("ix_quarantine_record_reason", table_name="quarantine_record")
    op.drop_table("quarantine_record")
    op.drop_table("normalized_unit")
    op.drop_table("normalized_document")
    op.drop_index("ix_acquired_asset_sha256", table_name="acquired_asset")
    op.drop_table("acquired_asset")
    op.drop_index("ix_source_metadata_source_identifier_observed", table_name="source_metadata_record")
    op.drop_table("source_metadata_record")
    op.drop_index("ix_robots_observation_host_observed", table_name="robots_observation")
    op.drop_table("robots_observation")
    op.drop_index("ix_policy_decision_source_action", table_name="policy_decision")
    op.drop_table("policy_decision")
