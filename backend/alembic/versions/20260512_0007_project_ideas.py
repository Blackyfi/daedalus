"""project ideas (Projects-page idea box)

Revision ID: 20260512_0007
Revises: 20260506_0006
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260512_0007"
down_revision: Union[str, None] = "20260506_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE project_idea_status AS ENUM ('new', 'promoted', 'archived');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.create_table(
        "project_ideas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "new",
                "promoted",
                "archived",
                name="project_idea_status",
                create_type=False,
            ),
            nullable=False,
            server_default="new",
        ),
        sa.Column(
            "promoted_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sort_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_project_ideas_owner_id", "project_ideas", ["owner_id"])
    op.create_index("ix_project_ideas_status", "project_ideas", ["status"])


def downgrade() -> None:
    op.drop_index("ix_project_ideas_status", table_name="project_ideas")
    op.drop_index("ix_project_ideas_owner_id", table_name="project_ideas")
    op.drop_table("project_ideas")
    op.execute("DROP TYPE IF EXISTS project_idea_status")
