"""SQLAlchemy ORM models — core Daedalus domain.

Mirrors the conceptual data model in §8.1 of project-plan.md.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daedalus.db.base import Base, TimestampMixin


# --- enums ---------------------------------------------------------------

class Role(str, enum.Enum):
    owner = "owner"
    member = "member"
    viewer = "viewer"


class TaskStatus(str, enum.Enum):
    backlog = "backlog"
    ready = "ready"
    in_progress = "in_progress"
    verifying = "verifying"
    needs_fixes = "needs_fixes"
    done = "done"
    cancelled = "cancelled"


class TaskPriority(str, enum.Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RunState(str, enum.Enum):
    queued = "queued"
    claimed = "claimed"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    aborted_unsafe = "aborted_unsafe"


class RunKind(str, enum.Enum):
    task = "task"
    argus = "argus"
    planning = "planning"
    cleanup = "cleanup"


class Verdict(str, enum.Enum):
    pass_ = "pass"
    partial = "partial"
    fail = "fail"


class PriorityLane(str, enum.Enum):
    urgent = "urgent"
    default = "default"
    bg = "bg"


class PlanProposalStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    discarded = "discarded"


# --- users ---------------------------------------------------------------

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False, default=Role.member)

    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # argon2id
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # base32, encrypted at rest
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_codes_hash: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pinned_cert_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)

    projects: Mapped[list[Project]] = relationship(back_populates="owner")
    webauthn_credentials: Mapped[list[WebAuthnCredential]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notification_pref: Mapped[UserNotificationPref | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


# --- notification preferences -------------------------------------------

class UserNotificationPref(Base, TimestampMixin):
    """Per-user channel toggles for each `NotificationKind`.

    A missing row is treated as all-on with no usage cap (see
    `daedalus.notifications.dispatcher._default_pref`). The mirrored
    `<channel>_<kind>` columns let the dispatcher resolve a single
    `getattr` per (kind, channel) instead of joining a child table.
    """

    __tablename__ = "user_notification_prefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    email_task_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_task_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_task_needs_fixes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_usage_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    in_app_task_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    in_app_task_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    in_app_task_needs_fixes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    in_app_usage_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Project cumulative `cost_usd_micros` ceiling that triggers a single
    # `usage_threshold` notification per crossing. NULL disables the gate.
    usage_threshold_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped[User] = relationship(back_populates="notification_pref")


# --- webauthn ------------------------------------------------------------

class WebAuthnCredential(Base, TimestampMixin):
    __tablename__ = "webauthn_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[str | None] = mapped_column(Text, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="webauthn_credentials")


# --- email otp -----------------------------------------------------------

class EmailOTP(Base, TimestampMixin):
    __tablename__ = "email_otps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)         # HMAC-SHA256 of cleartext code
    magic_token_hash: Mapped[str] = mapped_column(Text, nullable=False)  # HMAC of url-token
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    issued_cert_fp: Mapped[str | None] = mapped_column(String(128), nullable=True)


# --- sessions ------------------------------------------------------------

class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cert_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- projects ------------------------------------------------------------

class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)  # canonicalized inside workspaces_root
    git_default_branch: Mapped[str] = mapped_column(String(80), default="main", nullable=False)
    default_connector_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_fix_loops: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    auto_run_fix: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Project-scoped LLM overrides. NULL → fall back to LLM_MODEL / LLM_VERIFIER_MODEL.
    # task_model is injected as ANTHROPIC_MODEL into the connector subprocess env;
    # planning_model and verifier_model are read by the LLM client constructor.
    planning_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    task_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verifier_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    argus_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Per-project ceiling on wall-clock minutes; NULL → use the connector's value.
    wall_clock_minutes_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Auto-run quiet hours (hour-of-day, 0-23 in server local time). When both
    # start and end are non-null, the scheduler skips automatic re-queue inside
    # [start, end). Wrap-around (e.g. 22→6) is supported. NULL on either side
    # disables the gate.
    auto_run_quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_run_quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Maximum auto-runs per UTC day. 0 = unlimited (legacy behaviour).
    auto_run_daily_cap: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    owner: Mapped[User] = relationship(back_populates="projects")
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ideas: Mapped[list[Idea]] = relationship(back_populates="project", cascade="all, delete-orphan")
    notes: Mapped[list[Note]] = relationship(back_populates="project", cascade="all, delete-orphan")


# --- tasks ---------------------------------------------------------------

class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.backlog, nullable=False, index=True
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.P2, nullable=False, index=True
    )
    connector_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    profile: Mapped[str] = mapped_column(String(40), default="confirm", nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fix_loop_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_diff_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    project: Mapped[Project] = relationship(back_populates="tasks")
    runs: Mapped[list[Run]] = relationship(back_populates="task", cascade="all, delete-orphan")


# --- ideas / notes -------------------------------------------------------

class Idea(Base, TimestampMixin):
    __tablename__ = "ideas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project: Mapped[Project] = relationship(back_populates="ideas")


class Note(Base, TimestampMixin):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)

    project: Mapped[Project] = relationship(back_populates="notes")


# --- runs / argus reports ------------------------------------------------

class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[RunKind] = mapped_column(Enum(RunKind), nullable=False, default=RunKind.task)
    state: Mapped[RunState] = mapped_column(
        Enum(RunState), nullable=False, default=RunState.queued, index=True
    )
    lane: Mapped[PriorityLane] = mapped_column(
        Enum(PriorityLane), nullable=False, default=PriorityLane.default
    )
    connector_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When this run was created via /runs/{rid}/retry, this points at the
    # original (failed) run so the UI can show a retry chain and audits
    # can trace the lineage. NULL for first-attempt runs.
    retry_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    task: Mapped[Task | None] = relationship(back_populates="runs")
    argus_report: Mapped[ArgusReport | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class ArgusReport(Base, TimestampMixin):
    __tablename__ = "argus_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verdict: Mapped[Verdict] = mapped_column(Enum(Verdict, name="verdict"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    suggested_fix_task: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_output_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[Run] = relationship(back_populates="argus_report")


# --- plan proposals (idea-to-tasks review queue) ------------------------

class PlanProposal(Base, TimestampMixin):
    __tablename__ = "plan_proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[PlanProposalStatus] = mapped_column(
        Enum(PlanProposalStatus, name="planproposalstatus"),
        nullable=False,
        default=PlanProposalStatus.pending,
        index=True,
    )
    proposed_tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_idea_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- snapshots (pre-yolo workspace tarballs / git tags) -----------------

class Snapshot(Base, TimestampMixin):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    git_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    tarball_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- connectors ----------------------------------------------------------

class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("connector_id", name="uq_connectors_connector_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --- audit log -----------------------------------------------------------

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    actor_cert_fp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
