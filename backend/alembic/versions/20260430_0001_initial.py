"""initial schema

Revision ID: 20260430_0001
Revises:
Create Date: 2026-04-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260430_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_enum(name: str, *values: str) -> None:
    """Create a Postgres ENUM only if it doesn't already exist.

    Postgres doesn't ship `CREATE TYPE IF NOT EXISTS`, so we wrap in a
    DO-block that swallows `duplicate_object`. This is the single
    source of truth — `postgresql.ENUM(..., create_type=False)` on each
    column then prevents SQLAlchemy from emitting a *second* CREATE
    TYPE when the table is built.
    """
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$ BEGIN
            CREATE TYPE {name} AS ENUM ({quoted});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def _enum(name: str, *values: str) -> postgresql.ENUM:
    """A column-side ENUM reference that never tries to create the type."""
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    _ensure_enum("role", "owner", "member", "viewer")
    _ensure_enum(
        "taskstatus",
        "backlog", "ready", "in_progress", "verifying", "needs_fixes", "done", "cancelled",
    )
    _ensure_enum("taskpriority", "P0", "P1", "P2", "P3")
    _ensure_enum(
        "runstate",
        "queued", "claimed", "running", "completed", "failed", "cancelled", "aborted_unsafe",
    )
    _ensure_enum("runkind", "task", "argus", "planning", "cleanup")
    _ensure_enum("verdict", "pass", "partial", "fail")
    _ensure_enum("prioritylane", "urgent", "default", "bg")

    role_enum = _enum("role", "owner", "member", "viewer")
    task_status = _enum(
        "taskstatus",
        "backlog", "ready", "in_progress", "verifying", "needs_fixes", "done", "cancelled",
    )
    task_priority = _enum("taskpriority", "P0", "P1", "P2", "P3")
    run_state = _enum(
        "runstate",
        "queued", "claimed", "running", "completed", "failed", "cancelled", "aborted_unsafe",
    )
    run_kind = _enum("runkind", "task", "argus", "planning", "cleanup")
    verdict_enum = _enum("verdict", "pass", "partial", "fail")
    lane_enum = _enum("prioritylane", "urgent", "default", "bg")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", role_enum, nullable=False, server_default="member"),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True)),
        sa.Column("totp_secret", sa.Text),
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True)),
        sa.Column("recovery_codes_hash", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("pinned_cert_fingerprint", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "email_otps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False),
        sa.Column("magic_token_hash", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("issued_ip", postgresql.INET()),
        sa.Column("issued_cert_fp", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_otps_user_id", "email_otps", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cert_fingerprint", sa.String(128), nullable=False),
        sa.Column("issued_ip", postgresql.INET()),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("workspace_path", sa.Text, nullable=False),
        sa.Column("git_default_branch", sa.String(80), nullable=False, server_default="main"),
        sa.Column("default_connector_id", sa.String(120)),
        sa.Column("max_fix_loops", sa.Integer, nullable=False, server_default="3"),
        sa.Column("auto_run_fix", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("acceptance_criteria", sa.Text, nullable=False, server_default=""),
        sa.Column("status", task_status, nullable=False, server_default="backlog"),
        sa.Column("priority", task_priority, nullable=False, server_default="P2"),
        sa.Column("connector_id", sa.String(120)),
        sa.Column("profile", sa.String(40), nullable=False, server_default="confirm"),
        sa.Column("depends_on", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("tags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("estimated_minutes", sa.Integer),
        sa.Column("fix_loop_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_diff_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_priority", "tasks", ["priority"])

    op.create_table(
        "ideas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ideas_project_id", "ideas", ["project_id"])

    op.create_table(
        "notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notes_project_id", "notes", ["project_id"])

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tasks.id", ondelete="CASCADE")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", run_kind, nullable=False, server_default="task"),
        sa.Column("state", run_state, nullable=False, server_default="queued"),
        sa.Column("lane", lane_enum, nullable=False, server_default="default"),
        sa.Column("connector_snapshot", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("worktree_path", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer),
        sa.Column("transcript_object_key", sa.Text),
        sa.Column("diff_object_key", sa.Text),
        sa.Column("token_input", sa.Integer),
        sa.Column("token_output", sa.Integer),
        sa.Column("cost_usd_micros", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"])
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_state", "runs", ["state"])

    op.create_table(
        "argus_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", verdict_enum, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("findings", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("suggested_fix_task", postgresql.JSONB),
        sa.Column("raw_output_object_key", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_argus_reports_task_id", "argus_reports", ["task_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("git_tag", sa.Text),
        sa.Column("tarball_object_key", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_snapshots_project_id", "snapshots", ["project_id"])

    op.create_table(
        "connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_id", sa.String(120), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_connectors_connector_id", "connectors", ["connector_id"], unique=True)

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_ip", postgresql.INET()),
        sa.Column("actor_cert_fp", sa.String(128)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_kind", sa.String(40)),
        sa.Column("target_id", sa.String(80)),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    for tbl in (
        "audit_events", "connectors", "snapshots", "argus_reports", "runs",
        "notes", "ideas", "tasks", "projects", "sessions", "email_otps", "users",
    ):
        op.drop_table(tbl)
    for enum_name in ("prioritylane", "verdict", "runkind", "runstate", "taskpriority", "taskstatus", "role"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
