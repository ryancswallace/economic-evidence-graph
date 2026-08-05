"""durable acquisition failures

Revision ID: d32a7b419e10
Revises: c91d4d8e62a1
Create Date: 2026-08-04 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d32a7b419e10"  # pragma: allowlist secret
down_revision: str | None = "c91d4d8e62a1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable, source-record-scoped acquisition failures."""
    op.create_table(
        "acquisition_failure",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "source_record_id",
            sa.String(64),
            sa.ForeignKey("source_metadata_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_acquisition_failure_code_occurred",
        "acquisition_failure",
        ["failure_code", "occurred_at"],
    )


def downgrade() -> None:
    """Remove immutable acquisition failures."""
    op.drop_index("ix_acquisition_failure_code_occurred", table_name="acquisition_failure")
    op.drop_table("acquisition_failure")
