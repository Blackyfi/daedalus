"""rate-limit forensics columns on runs

Revision ID: 20260506_0007
Revises: 20260506_0006
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260506_0007"
down_revision: Union[str, None] = "20260506_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("was_rate_limited", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "runs",
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "retry_after")
    op.drop_column("runs", "was_rate_limited")
