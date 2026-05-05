"""runs.retry_of column

Revision ID: 20260503_0004
Revises: 20260503_0003
Create Date: 2026-05-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260503_0004"
down_revision: Union[str, None] = "20260503_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "retry_of",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_runs_retry_of", "runs", ["retry_of"])


def downgrade() -> None:
    op.drop_index("ix_runs_retry_of", table_name="runs")
    op.drop_column("runs", "retry_of")
