"""auto-run quiet hours

Revision ID: 20260507_0006
Revises: 20260504_0005
Create Date: 2026-05-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260507_0006"
down_revision: Union[str, None] = "20260504_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("auto_run_quiet_hours_start", sa.Integer, nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("auto_run_quiet_hours_end", sa.Integer, nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_daily_cap",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_projects_auto_run_quiet_start_range",
        "projects",
        "auto_run_quiet_hours_start IS NULL OR (auto_run_quiet_hours_start >= 0 AND auto_run_quiet_hours_start <= 23)",
    )
    op.create_check_constraint(
        "ck_projects_auto_run_quiet_end_range",
        "projects",
        "auto_run_quiet_hours_end IS NULL OR (auto_run_quiet_hours_end >= 0 AND auto_run_quiet_hours_end <= 23)",
    )
    op.create_check_constraint(
        "ck_projects_auto_run_daily_cap_nonneg",
        "projects",
        "auto_run_daily_cap >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_auto_run_daily_cap_nonneg", "projects", type_="check")
    op.drop_constraint("ck_projects_auto_run_quiet_end_range", "projects", type_="check")
    op.drop_constraint("ck_projects_auto_run_quiet_start_range", "projects", type_="check")
    op.drop_column("projects", "auto_run_daily_cap")
    op.drop_column("projects", "auto_run_quiet_hours_end")
    op.drop_column("projects", "auto_run_quiet_hours_start")
