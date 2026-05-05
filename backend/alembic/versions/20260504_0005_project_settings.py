"""project-level model + agent overrides

Revision ID: 20260504_0005
Revises: 20260503_0004
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260504_0005"
down_revision: Union[str, None] = "20260503_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("planning_model", sa.String(120), nullable=True))
    op.add_column("projects", sa.Column("task_model", sa.String(120), nullable=True))
    op.add_column("projects", sa.Column("verifier_model", sa.String(120), nullable=True))
    op.add_column(
        "projects",
        sa.Column("argus_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "projects",
        sa.Column("wall_clock_minutes_override", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "wall_clock_minutes_override")
    op.drop_column("projects", "argus_enabled")
    op.drop_column("projects", "verifier_model")
    op.drop_column("projects", "task_model")
    op.drop_column("projects", "planning_model")
