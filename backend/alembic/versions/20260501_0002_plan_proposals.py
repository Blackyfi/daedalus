"""plan_proposals + planproposalstatus enum

Revision ID: 20260501_0002
Revises: 20260430_0001
Create Date: 2026-05-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260501_0002"
down_revision: str | None = "20260430_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE planproposalstatus AS ENUM ('pending', 'confirmed', 'discarded');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    plan_status = postgresql.ENUM(
        "pending", "confirmed", "discarded",
        name="planproposalstatus",
        create_type=False,
    )

    op.create_table(
        "plan_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
        ),
        sa.Column("status", plan_status, nullable=False, server_default="pending"),
        sa.Column("proposed_tasks", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "source_idea_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False, server_default="{}",
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_plan_proposals_project_id", "plan_proposals", ["project_id"])
    op.create_index("ix_plan_proposals_status", "plan_proposals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_plan_proposals_status", table_name="plan_proposals")
    op.drop_index("ix_plan_proposals_project_id", table_name="plan_proposals")
    op.drop_table("plan_proposals")
    op.execute("DROP TYPE IF EXISTS planproposalstatus")
