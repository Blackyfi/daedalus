"""merge_batches + merge_batch_items

Revision ID: 20260506_0006
Revises: 20260504_0005
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260506_0006"
down_revision: Union[str, None] = "20260504_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BATCH_STATES = (
    "pending",
    "merging_clean",
    "awaiting_review",
    "resolving",
    "shipping",
    "shipped",
    "failed",
    "aborted",
)
ITEM_STATES = (
    "pending",
    "merged",
    "skipped_empty",
    "skipped_already_merged",
    "skipped_missing",
    "skipped_conflict",
    "resolution_queued",
    "resolution_running",
    "resolved",
    "resolution_failed",
)
ITEM_CATEGORIES = (
    "clean",
    "conflict",
    "empty",
    "already_merged",
    "missing_branch",
    "missing_run",
)


def _ensure_enum(name: str, values: tuple[str, ...]) -> None:
    """Create a Postgres ENUM idempotently. Matches the pattern used by the
    initial migration so re-running on a partially-applied schema is safe."""
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$ BEGIN
            CREATE TYPE {name} AS ENUM ({quoted});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def upgrade() -> None:
    _ensure_enum("merge_batch_state", BATCH_STATES)
    _ensure_enum("merge_item_state", ITEM_STATES)
    _ensure_enum("merge_item_category", ITEM_CATEGORIES)

    batch_state = postgresql.ENUM(*BATCH_STATES, name="merge_batch_state", create_type=False)
    item_state = postgresql.ENUM(*ITEM_STATES, name="merge_item_state", create_type=False)
    item_cat = postgresql.ENUM(*ITEM_CATEGORIES, name="merge_item_category", create_type=False)

    op.create_table(
        "merge_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("integration_branch", sa.Text, nullable=False),
        sa.Column("integration_worktree", sa.Text, nullable=False),
        sa.Column("state", batch_state, nullable=False, server_default="pending"),
        sa.Column("verify_exit_code", sa.Integer, nullable=True),
        sa.Column("verify_output", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("require_argus_pass", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "merge_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merge_batches.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolution_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolution_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("branch", sa.Text, nullable=False),
        sa.Column("category", item_cat, nullable=False),
        sa.Column("state", item_state, nullable=False, server_default="pending"),
        sa.Column("conflicting_files", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("commits_ahead", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("merge_batch_items")
    op.drop_table("merge_batches")
    bind = op.get_bind()
    postgresql.ENUM(name="merge_item_category").drop(bind, checkfirst=True)
    postgresql.ENUM(name="merge_item_state").drop(bind, checkfirst=True)
    postgresql.ENUM(name="merge_batch_state").drop(bind, checkfirst=True)
