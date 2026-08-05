"""projects and evidence collaboration

Revision ID: f31b8c9d4a70
Revises: e8471ac95f20
Create Date: 2026-08-05 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f31b8c9d4a70"  # pragma: allowlist secret
down_revision: str | None = "e8471ac95f20"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add collaborative projects, review history, evidence tables, links, and exports."""
    op.create_table(
        "project",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_project_positive_revision"),
        sa.UniqueConstraint("created_by", "idempotency_key", name="uq_project_creator_operation"),
    )
    op.create_index("ix_project_created", "project", ["created_at"])
    op.create_table(
        "project_member",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "actor_id", name="uq_project_member_actor"),
    )
    op.create_index("ix_project_member_actor", "project_member", ["actor_id", "project_id"])
    op.create_table(
        "project_search_run",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "search_run_id",
            sa.String(64),
            sa.ForeignKey("search_run.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "search_run_id", name="uq_project_search_run_attachment"),
    )
    op.create_table(
        "screening_event",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_screening_project_work",
        "screening_event",
        ["project_id", "work_id", "decided_at"],
    )
    op.create_table(
        "project_note",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_project_note_target",
        "project_note",
        ["project_id", "target_type", "target_id"],
    )
    op.create_table(
        "project_evidence_record",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", sa.String(64), nullable=False),
        sa.Column("document_version_id", sa.String(64), nullable=False),
        sa.Column("record_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_project_evidence_work",
        "project_evidence_record",
        ["project_id", "work_id"],
    )
    op.create_table(
        "evidence_table",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_table(
        "evidence_table_row",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_id", sa.String(64), sa.ForeignKey("evidence_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "evidence_record_id",
            sa.String(64),
            sa.ForeignKey("project_evidence_record.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("table_id", "evidence_record_id", name="uq_evidence_table_record_row"),
    )
    op.create_table(
        "policy_link",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("research_work_id", sa.String(64), nullable=False),
        sa.Column("policy_work_id", sa.String(64), nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_policy_link_research", "policy_link", ["project_id", "research_work_id"])
    op.create_index("ix_policy_link_policy", "policy_link", ["project_id", "policy_work_id"])
    op.create_table(
        "review_event",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_review_event_target",
        "review_event",
        ["project_id", "target_type", "target_id"],
    )
    op.create_table(
        "project_export",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_id", sa.String(64), sa.ForeignKey("evidence_table.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_project_export_created", "project_export", ["project_id", "created_at"])


def downgrade() -> None:
    """Remove Step 4 project and evidence tables in dependency order."""
    op.drop_index("ix_project_export_created", table_name="project_export")
    op.drop_table("project_export")
    op.drop_index("ix_review_event_target", table_name="review_event")
    op.drop_table("review_event")
    op.drop_index("ix_policy_link_policy", table_name="policy_link")
    op.drop_index("ix_policy_link_research", table_name="policy_link")
    op.drop_table("policy_link")
    op.drop_table("evidence_table_row")
    op.drop_table("evidence_table")
    op.drop_index("ix_project_evidence_work", table_name="project_evidence_record")
    op.drop_table("project_evidence_record")
    op.drop_index("ix_project_note_target", table_name="project_note")
    op.drop_table("project_note")
    op.drop_index("ix_screening_project_work", table_name="screening_event")
    op.drop_table("screening_event")
    op.drop_table("project_search_run")
    op.drop_index("ix_project_member_actor", table_name="project_member")
    op.drop_table("project_member")
    op.drop_index("ix_project_created", table_name="project")
    op.drop_table("project")
