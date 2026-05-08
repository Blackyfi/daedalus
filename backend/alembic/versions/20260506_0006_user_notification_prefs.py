"""user notification preferences

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


def upgrade() -> None:
    op.create_table(
        "user_notification_prefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("email_task_completed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("email_task_failed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("email_task_needs_fixes", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("email_usage_threshold", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("in_app_task_completed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("in_app_task_failed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("in_app_task_needs_fixes", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("in_app_usage_threshold", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("usage_threshold_micros", sa.BigInteger, nullable=True),
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


def downgrade() -> None:
    op.drop_table("user_notification_prefs")
