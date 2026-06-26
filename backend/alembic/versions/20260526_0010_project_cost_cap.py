"""project monthly cost cap

Revision ID: 20260526_0010
Revises: 20260512_0009
Create Date: 2026-05-26

Adds projects.monthly_cost_cap_usd_micros (nullable; null = no cap).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "20260526_0010"
down_revision: str | None = "20260512_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("monthly_cost_cap_usd_micros", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "monthly_cost_cap_usd_micros")
