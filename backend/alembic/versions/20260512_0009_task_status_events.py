"""task_status_events table + backfill from existing tasks

Revision ID: 20260512_0009
Revises: 20260510_0008
Create Date: 2026-05-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260512_0009"
down_revision: str | None = "20260510_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The taskstatus enum already exists from migration 0001 — reference it
    # without trying to recreate.
    task_status = postgresql.ENUM(
        "backlog", "ready", "in_progress", "verifying", "needs_fixes", "done", "cancelled",
        name="taskstatus", create_type=False,
    )

    op.create_table(
        "task_status_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", task_status, nullable=True),
        sa.Column("to_status", task_status, nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_task_status_events_task_id", "task_status_events", ["task_id"]
    )
    op.create_index(
        "ix_task_status_events_project_id", "task_status_events", ["project_id"]
    )
    op.create_index("ix_task_status_events_at", "task_status_events", ["at"])
    # Hot path for "latest status per task at time T" — DISTINCT ON
    # (task_id) ORDER BY task_id, at DESC.
    op.create_index(
        "ix_task_status_events_project_task_at",
        "task_status_events",
        ["project_id", "task_id", sa.text("at DESC")],
    )

    # Backfill: every task gets a creation event (NULL -> backlog at
    # created_at). Tasks whose current status isn't backlog also get a
    # second event approximating the transition at updated_at. This is
    # lossy for tasks that moved through multiple statuses (we only see
    # the final one) but gives a usable 2-point series until real events
    # accumulate.
    op.execute(
        """
        INSERT INTO task_status_events (id, task_id, project_id, from_status, to_status, at)
        SELECT
            gen_random_uuid(),
            t.id,
            t.project_id,
            NULL,
            'backlog'::taskstatus,
            t.created_at
        FROM tasks t
        """
    )
    op.execute(
        """
        INSERT INTO task_status_events (id, task_id, project_id, from_status, to_status, at)
        SELECT
            gen_random_uuid(),
            t.id,
            t.project_id,
            'backlog'::taskstatus,
            t.status,
            -- guard against (rare) updated_at <= created_at: bump by 1ms so
            -- the "latest" event at time T orders deterministically.
            GREATEST(t.updated_at, t.created_at + interval '1 millisecond')
        FROM tasks t
        WHERE t.status <> 'backlog'::taskstatus
        """
    )


def downgrade() -> None:
    op.drop_index("ix_task_status_events_project_task_at", table_name="task_status_events")
    op.drop_index("ix_task_status_events_at", table_name="task_status_events")
    op.drop_index("ix_task_status_events_project_id", table_name="task_status_events")
    op.drop_index("ix_task_status_events_task_id", table_name="task_status_events")
    op.drop_table("task_status_events")
