"""audit_events.entry_hash column (tamper-evidence)

Revision ID: 20260626_0012
Revises: 20260626_0011
Create Date: 2026-06-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260626_0012"
down_revision: str | None = "20260626_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("entry_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "entry_hash")
