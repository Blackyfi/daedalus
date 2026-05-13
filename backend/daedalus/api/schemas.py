"""Pydantic v2 request/response schemas. Source-of-truth for the OpenAPI surface."""
from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator

from daedalus.db.models import (
    PlanProposalStatus,
    PriorityLane,
    ProjectIdeaStatus,
    Role,
    RunKind,
    RunState,
    TaskPriority,
    TaskStatus,
    Verdict,
)


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- user ---

class UserOut(_Base):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: Role
    last_login_at: datetime | None
    totp_enrolled_at: datetime | None


# --- project ---

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    workspace_path: str = Field(min_length=1)
    git_default_branch: str = "main"
    default_connector_id: str | None = None
    max_fix_loops: int = Field(3, ge=0, le=20)
    auto_run_fix: bool = False
    planning_model: str | None = Field(default=None, max_length=120)
    task_model: str | None = Field(default=None, max_length=120)
    verifier_model: str | None = Field(default=None, max_length=120)
    argus_enabled: bool = True
    wall_clock_minutes_override: int | None = Field(default=None, ge=1, le=1440)
    auto_run_quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    auto_run_quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    auto_run_daily_cap: int = Field(default=0, ge=0, le=500)
    auto_run_concurrency_cap: int = Field(default=1, ge=0, le=64)
    auto_run_hourly_cap: int = Field(default=0, ge=0, le=500)
    auto_run_allowed_connectors: list[str] = Field(default_factory=list)
    auto_run_eligible_statuses: list[TaskStatus] = Field(
        default_factory=lambda: [
            TaskStatus.backlog,
            TaskStatus.ready,
            TaskStatus.needs_fixes,
        ]
    )


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    git_default_branch: str | None = None
    default_connector_id: str | None = None
    max_fix_loops: int | None = Field(default=None, ge=0, le=20)
    auto_run_fix: bool | None = None
    archived: bool | None = None
    planning_model: str | None = Field(default=None, max_length=120)
    task_model: str | None = Field(default=None, max_length=120)
    verifier_model: str | None = Field(default=None, max_length=120)
    argus_enabled: bool | None = None
    wall_clock_minutes_override: int | None = Field(default=None, ge=1, le=1440)
    auto_run_quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    auto_run_quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    auto_run_daily_cap: int | None = Field(default=None, ge=0, le=500)
    auto_run_concurrency_cap: int | None = Field(default=None, ge=0, le=64)
    auto_run_hourly_cap: int | None = Field(default=None, ge=0, le=500)
    auto_run_allowed_connectors: list[str] | None = None
    auto_run_eligible_statuses: list[TaskStatus] | None = None


class ProjectOut(_Base):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    workspace_path: str
    git_default_branch: str
    default_connector_id: str | None
    max_fix_loops: int
    auto_run_fix: bool
    archived: bool
    planning_model: str | None
    task_model: str | None
    verifier_model: str | None
    argus_enabled: bool
    wall_clock_minutes_override: int | None
    auto_run_quiet_hours_start: int | None
    auto_run_quiet_hours_end: int | None
    auto_run_daily_cap: int
    auto_run_concurrency_cap: int
    auto_run_hourly_cap: int
    auto_run_allowed_connectors: list[str]
    auto_run_eligible_statuses: list[str]
    created_at: datetime
    updated_at: datetime


# --- auto-run ---

class AutoRunConfigPatch(BaseModel):
    """Subset of project settings exposed by the AutoRun panel.

    All fields are optional so the panel can PATCH partial updates.
    """
    auto_run_fix: bool | None = None
    max_fix_loops: int | None = Field(default=None, ge=0, le=20)
    wall_clock_minutes_override: int | None = Field(default=None, ge=1, le=1440)
    default_connector_id: str | None = None
    auto_run_quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    auto_run_quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    auto_run_daily_cap: int | None = Field(default=None, ge=0, le=500)
    auto_run_concurrency_cap: int | None = Field(default=None, ge=0, le=64)
    auto_run_hourly_cap: int | None = Field(default=None, ge=0, le=500)
    auto_run_allowed_connectors: list[str] | None = None
    auto_run_eligible_statuses: list[TaskStatus] | None = None


class AutoRunRecentRun(_Base):
    id: uuid.UUID
    task_id: uuid.UUID | None
    task_title: str | None
    state: str
    kind: str
    started_at: datetime | None
    finished_at: datetime | None
    auto_triggered: bool
    created_at: datetime


class AutoRunStatusOut(BaseModel):
    project_id: uuid.UUID
    enabled: bool
    max_fix_loops: int
    wall_clock_minutes_override: int | None
    default_connector_id: str | None
    auto_run_quiet_hours_start: int | None
    auto_run_quiet_hours_end: int | None
    auto_run_daily_cap: int
    auto_run_concurrency_cap: int
    auto_run_hourly_cap: int
    auto_run_allowed_connectors: list[str]
    auto_run_eligible_statuses: list[str]
    # Effective list the scheduler will actually consider — same as
    # auto_run_eligible_statuses but echoed under the legacy key so older
    # clients keep working.
    eligible_task_statuses: list[str]
    in_quiet_hours: bool
    runs_today: int
    runs_last_hour: int
    active_auto_runs: int
    daily_cap_remaining: int | None
    hourly_cap_remaining: int | None
    concurrency_remaining: int | None
    recent_runs: list[AutoRunRecentRun]


# --- auto-run defaults (global / org-wide) ---

class AutoRunDefaultsOut(_Base):
    """Org-wide default auto-run policy surfaced on Account/admin."""

    enabled: bool
    max_fix_loops: int
    daily_cap: int
    hourly_cap: int
    concurrency_cap: int
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    eligible_statuses: list[str]
    allowed_connectors: list[str]
    updated_at: datetime


class AutoRunDefaultsPatch(BaseModel):
    """Partial update to the global auto-run defaults singleton.

    Owner-only. All fields optional. Cross-field validation (quiet-hours
    pair, etc.) is performed by the route handler.
    """

    enabled: bool | None = None
    max_fix_loops: int | None = Field(default=None, ge=0, le=20)
    daily_cap: int | None = Field(default=None, ge=0, le=500)
    hourly_cap: int | None = Field(default=None, ge=0, le=500)
    concurrency_cap: int | None = Field(default=None, ge=0, le=64)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    eligible_statuses: list[TaskStatus] | None = None
    allowed_connectors: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


# --- task ---

class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    acceptance_criteria: str = ""
    priority: TaskPriority = TaskPriority.P2
    connector_id: str | None = None
    profile: str = "confirm"
    depends_on: list[uuid.UUID] = []
    tags: list[str] = []
    estimated_minutes: int | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    acceptance_criteria: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    connector_id: str | None = None
    profile: str | None = None
    depends_on: list[uuid.UUID] | None = None
    tags: list[str] | None = None
    estimated_minutes: int | None = None


class TaskOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    title: str
    description: str
    acceptance_criteria: str
    status: TaskStatus
    priority: TaskPriority
    connector_id: str | None
    profile: str
    depends_on: list[uuid.UUID]
    tags: list[str]
    estimated_minutes: int | None
    fix_loop_count: int
    created_at: datetime
    updated_at: datetime


# --- idea ---

class IdeaIn(BaseModel):
    text: str = Field(min_length=1)
    tags: list[str] = []


class IdeaPatch(BaseModel):
    # Accept either `text` or `body` so callers can reuse the note-style
    # field name; both map to `Idea.text` on the model.
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("text", "body"),
    )
    tags: list[str] | None = None
    sort_index: int | None = None


class IdeaOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    text: str
    tags: list[str]
    archived: bool
    sort_index: int
    created_at: datetime
    updated_at: datetime


# --- project idea (Projects-page idea box) ---


class ProjectIdeaIn(BaseModel):
    text: str = Field(min_length=1)
    tags: list[str] = []


class ProjectIdeaPatch(BaseModel):
    """Inline-edit / archive surface. Promotion has its own endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    text: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("text", "body"),
    )
    tags: list[str] | None = None
    sort_index: int | None = None
    # Allow flipping between `new` and `archived`; `promoted` is owned
    # by the dedicated promote endpoint and is rejected here.
    status: ProjectIdeaStatus | None = None


class ProjectIdeaOut(_Base):
    id: uuid.UUID
    owner_id: uuid.UUID
    text: str
    tags: list[str]
    status: ProjectIdeaStatus
    promoted_project_id: uuid.UUID | None
    sort_index: int
    created_at: datetime
    updated_at: datetime


class ProjectIdeaPromote(BaseModel):
    """Payload accepted by `POST /project-ideas/{id}/promote`.

    Mirrors the new-project form so the SPA can pre-fill the modal
    from the idea row before promotion.
    """

    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    workspace_path: str = Field(min_length=1)
    git_default_branch: str = "main"
    default_connector_id: str | None = None
    init_git: bool = False


# --- note ---

class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    body: str = ""


class NotePatch(BaseModel):
    title: str | None = None
    body: str | None = None


class NoteOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    body: str
    created_at: datetime
    updated_at: datetime


# --- connector ---

class ConnectorIn(BaseModel):
    spec: dict[str, Any]


class ConnectorOut(_Base):
    id: uuid.UUID
    connector_id: str
    display_name: str
    spec: dict[str, Any]
    schema_version: int
    enabled: bool


# --- run ---

class RunOut(_Base):
    id: uuid.UUID
    task_id: uuid.UUID | None
    project_id: uuid.UUID
    kind: RunKind
    state: RunState
    lane: PriorityLane
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    token_input: int | None
    token_output: int | None
    cost_usd_micros: int | None
    retry_of: uuid.UUID | None


class InjectIn(BaseModel):
    text: str = Field(min_length=1, max_length=8192)


class ResizeIn(BaseModel):
    rows: int = Field(ge=1, le=500)
    cols: int = Field(ge=1, le=1000)


class SnapshotOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    git_tag: str | None
    tarball_object_key: str | None
    note: str | None
    created_at: datetime


# --- argus ---

class ArgusFinding(BaseModel):
    severity: str
    category: str
    description: str
    evidence: str | None = None


class ArgusOut(_Base):
    id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID
    verdict: Verdict
    summary: str
    findings: list[ArgusFinding]
    suggested_fix_task: dict[str, Any] | None
    created_at: datetime


# --- planning ---

class ProposedTask(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: str = ""
    priority: TaskPriority = TaskPriority.P2
    depends_on: list[int] = []  # indexes into the proposal list — resolved on confirm
    suggested_connector: str | None = None
    tags: list[str] = []
    source_idea_id: uuid.UUID | None = None


class PlanProposalOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    status: PlanProposalStatus
    proposed_tasks: list[ProposedTask]
    rationale: str
    source_idea_ids: list[uuid.UUID]
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanConfirm(BaseModel):
    """Optional edits to apply when confirming a proposal."""

    proposed_tasks: list[ProposedTask] | None = None
    rationale: str | None = None
    archive_source_ideas: bool = True


# --- discovery ---

class DiscoveredRepoOut(BaseModel):
    name: str
    path: str
    relative_path: str
    default_branch: str
    description: str
    last_commit_at: datetime | None
    has_uncommitted: bool
    already_registered: bool


class DiscoverRepoEntry(BaseModel):
    """One entry in a bulk-register payload."""

    path: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=160)
    description: str | None = None
    git_default_branch: str | None = None
    default_connector_id: str | None = None


class DiscoverRegisterIn(BaseModel):
    repos: list[DiscoverRepoEntry] = Field(min_length=1)


# --- notification preferences ---

class NotificationPrefsOut(_Base):
    """Mirrors `UserNotificationPref` with friendly defaults applied.

    Returned even when the user has no row in `user_notification_prefs`
    yet — in that case the dispatcher's "all on" defaults are reflected
    so the SPA can render a coherent toggle state.
    """

    email_task_completed: bool
    email_task_failed: bool
    email_task_needs_fixes: bool
    email_usage_threshold: bool
    in_app_task_completed: bool
    in_app_task_failed: bool
    in_app_task_needs_fixes: bool
    in_app_usage_threshold: bool
    usage_threshold_micros: int | None


class NotificationPrefsPatch(BaseModel):
    """Partial update: only fields explicitly set are applied.

    `usage_threshold_micros` is a project cumulative ceiling expressed in
    micro-USD (1 USD = 1_000_000). Pass `null` to remove the gate.
    """

    email_task_completed: bool | None = None
    email_task_failed: bool | None = None
    email_task_needs_fixes: bool | None = None
    email_usage_threshold: bool | None = None
    in_app_task_completed: bool | None = None
    in_app_task_failed: bool | None = None
    in_app_task_needs_fixes: bool | None = None
    in_app_usage_threshold: bool | None = None
    usage_threshold_micros: int | None = Field(
        default=None, ge=0, le=10_000_000_000
    )

    model_config = ConfigDict(extra="forbid")


# --- audit ---

class AuditOut(_Base):
    id: uuid.UUID
    at: datetime
    actor_user_id: uuid.UUID | None
    # asyncpg hands back ipaddress.IPv4Address / IPv6Address for INET columns;
    # the validator coerces them into the wire-friendly string the SPA expects.
    actor_ip: str | None
    actor_cert_fp: str | None
    action: str
    target_kind: str | None
    target_id: str | None
    payload: dict[str, Any]

    @field_validator("actor_ip", mode="before")
    @classmethod
    def _ip_to_str(cls, v: Any) -> Any:
        if isinstance(v, (IPv4Address, IPv6Address)):
            return str(v)
        return v
