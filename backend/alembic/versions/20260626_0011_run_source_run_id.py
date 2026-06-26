"""runs.source_run_id column (argus run -> verified task run)

Revision ID: 20260626_0011
Revises: 20260526_0010
Create Date: 2026-06-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260626_0011"
down_revision: str | None = "20260526_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_runs_source_run_id", "runs", ["source_run_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_source_run_id", table_name="runs")
    op.drop_column("runs", "source_run_id")
