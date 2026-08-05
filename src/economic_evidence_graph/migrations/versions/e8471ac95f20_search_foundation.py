"""search and historical retrieval foundation

Revision ID: e8471ac95f20
Revises: d32a7b419e10
Create Date: 2026-08-04 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8471ac95f20"  # pragma: allowlist secret
down_revision: str | None = "d32a7b419e10"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable index generations and saved search-run manifests."""
    op.create_table(
        "index_generation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mapping_version", sa.String(128), nullable=False),
        sa.Column("retrieval_config_id", sa.String(128), nullable=False),
        sa.Column("embedding_config_id", sa.String(128), nullable=False),
        sa.Column("corpus_snapshot_id", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("work_index_name", sa.String(255), nullable=False, unique=True),
        sa.Column("evidence_index_name", sa.String(255), nullable=False, unique=True),
        sa.Column("work_document_count", sa.Integer(), nullable=False),
        sa.Column("evidence_document_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("work_document_count >= 0", name="ck_index_generation_nonnegative_work_count"),
        sa.CheckConstraint(
            "evidence_document_count >= 0",
            name="ck_index_generation_nonnegative_evidence_count",
        ),
    )
    op.create_index("ix_index_generation_activated", "index_generation", ["activated_at"])
    op.create_table(
        "search_run",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "index_generation_id",
            sa.String(64),
            sa.ForeignKey("index_generation.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("query_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_search_run_generation_created",
        "search_run",
        ["index_generation_id", "created_at"],
    )


def downgrade() -> None:
    """Remove search-run and index-generation persistence."""
    op.drop_index("ix_search_run_generation_created", table_name="search_run")
    op.drop_table("search_run")
    op.drop_index("ix_index_generation_activated", table_name="index_generation")
    op.drop_table("index_generation")
