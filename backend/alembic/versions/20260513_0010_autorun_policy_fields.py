"""auto-run policy fields + global defaults singleton

Revision ID: 20260513_0010
Revises: 20260512_0009
Create Date: 2026-05-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_0010"
down_revision: Union[str, None] = "20260512_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_concurrency_cap",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_hourly_cap",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_allowed_connectors",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_eligible_statuses",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{backlog,ready,needs_fixes}",
        ),
    )
    op.create_check_constraint(
        "ck_projects_auto_run_concurrency_cap_nonneg",
        "projects",
        "auto_run_concurrency_cap >= 0",
    )
    op.create_check_constraint(
        "ck_projects_auto_run_hourly_cap_nonneg",
        "projects",
        "auto_run_hourly_cap >= 0",
    )

    op.create_table(
        "auto_run_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_fix_loops", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hourly_cap", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("concurrency_cap", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("quiet_hours_start", sa.Integer(), nullable=True),
        sa.Column("quiet_hours_end", sa.Integer(), nullable=True),
        sa.Column(
            "eligible_statuses",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{backlog,ready,needs_fixes}",
        ),
        sa.Column(
            "allowed_connectors",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
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
        sa.CheckConstraint("id = 1", name="ck_auto_run_defaults_singleton"),
        sa.CheckConstraint(
            "quiet_hours_start IS NULL OR (quiet_hours_start BETWEEN 0 AND 23)",
            name="ck_auto_run_defaults_quiet_start",
        ),
        sa.CheckConstraint(
            "quiet_hours_end IS NULL OR (quiet_hours_end BETWEEN 0 AND 23)",
            name="ck_auto_run_defaults_quiet_end",
        ),
        sa.CheckConstraint(
            "max_fix_loops >= 0 AND max_fix_loops <= 20",
            name="ck_auto_run_defaults_max_fix_loops",
        ),
        sa.CheckConstraint("daily_cap >= 0", name="ck_auto_run_defaults_daily_cap"),
        sa.CheckConstraint("hourly_cap >= 0", name="ck_auto_run_defaults_hourly_cap"),
        sa.CheckConstraint(
            "concurrency_cap >= 0", name="ck_auto_run_defaults_concurrency_cap"
        ),
    )
    # Seed the singleton row with stock defaults so the operator UI always
    # has something to GET. PATCH will upsert into this same id=1 row.
    op.execute(
        "INSERT INTO auto_run_defaults (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("auto_run_defaults")
    op.drop_constraint(
        "ck_projects_auto_run_hourly_cap_nonneg", "projects", type_="check"
    )
    op.drop_constraint(
        "ck_projects_auto_run_concurrency_cap_nonneg", "projects", type_="check"
    )
    op.drop_column("projects", "auto_run_eligible_statuses")
    op.drop_column("projects", "auto_run_allowed_connectors")
    op.drop_column("projects", "auto_run_hourly_cap")
    op.drop_column("projects", "auto_run_concurrency_cap")
