"""connector-level overrides that force project settings

Revision ID: 20260510_0008
Revises: 20260506_0007
Create Date: 2026-05-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260510_0008"
down_revision: Union[str, None] = "20260506_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column(
            "force_project_overrides",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("connectors", sa.Column("override_planning_model", sa.String(120), nullable=True))
    op.add_column("connectors", sa.Column("override_task_model", sa.String(120), nullable=True))
    op.add_column("connectors", sa.Column("override_verifier_model", sa.String(120), nullable=True))
    op.add_column("connectors", sa.Column("override_wall_clock_minutes", sa.Integer, nullable=True))
    op.add_column("connectors", sa.Column("override_argus_enabled", sa.Boolean, nullable=True))
    op.add_column("connectors", sa.Column("override_max_fix_loops", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("connectors", "override_max_fix_loops")
    op.drop_column("connectors", "override_argus_enabled")
    op.drop_column("connectors", "override_wall_clock_minutes")
    op.drop_column("connectors", "override_verifier_model")
    op.drop_column("connectors", "override_task_model")
    op.drop_column("connectors", "override_planning_model")
    op.drop_column("connectors", "force_project_overrides")
