"""tool invocation audit

Revision ID: a64c09e18b2d
Revises: f31b8c9d4a70
Create Date: 2026-08-06 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a64c09e18b2d"  # pragma: allowlist secret
down_revision: str | None = "f31b8c9d4a70"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add privacy-safe append-only audit for all agent-tool interfaces."""
    op.create_table(
        "tool_invocation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments_sha256", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("duration_ms >= 0", name="ck_tool_invocation_nonnegative_duration_ms"),
    )
    op.create_index(
        "ix_tool_invocation_actor_time",
        "tool_invocation",
        ["actor_id", "occurred_at"],
    )
    op.create_index(
        "ix_tool_invocation_tool_outcome",
        "tool_invocation",
        ["tool_name", "outcome"],
    )


def downgrade() -> None:
    """Remove tool audit records and indexes."""
    op.drop_index("ix_tool_invocation_tool_outcome", table_name="tool_invocation")
    op.drop_index("ix_tool_invocation_actor_time", table_name="tool_invocation")
    op.drop_table("tool_invocation")
