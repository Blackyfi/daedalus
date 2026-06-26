"""merge_batches.pre_ship_oid column (ship undo)

Revision ID: 20260626_0013
Revises: 20260626_0012
Create Date: 2026-06-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260626_0013"
down_revision: str | None = "20260626_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("merge_batches", sa.Column("pre_ship_oid", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("merge_batches", "pre_ship_oid")
