"""Auto-run configuration endpoints — backs the AutoRun panel.

These routes thin-wrap the relevant subset of the project record (toggle,
caps, default connector, quiet hours, daily cap) and add three derived bits
the panel needs that the bare project resource doesn't surface:

  * the task statuses that auto-run will pick up, so the panel can render
    the eligibility list without hard-coding scheduler internals
  * a recent-runs list with an `auto_triggered` flag — runs that the
    scheduler enqueued itself rather than ones the user clicked
  * window utilization (runs today, runs in last hour, active auto-runs)
    so the panel can render a live indicator

A second resource — the `/defaults` singleton — is the org-wide policy
the Account/admin page exposes. It is owner-only and seeds new projects.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import (
    AutoRunConfigPatch,
    AutoRunDefaultsOut,
    AutoRunDefaultsPatch,
    AutoRunRecentRun,
    AutoRunStatusOut,
)
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import (
    AutoRunDefaults,
    Project,
    Role,
    Run,
    RunState,
    Task,
    TaskStatus,
    User,
)

router = APIRouter()


# Default eligible statuses when a project (legacy data) has no list set.
DEFAULT_ELIGIBLE_TASK_STATUSES: tuple[str, ...] = (
    TaskStatus.backlog.value,
    TaskStatus.ready.value,
    TaskStatus.needs_fixes.value,
)

# Run states that still count toward "active auto-run" concurrency.
ACTIVE_RUN_STATES: tuple[RunState, ...] = (
    RunState.queued,
    RunState.claimed,
    RunState.running,
)


def _in_quiet_hours(start: int | None, end: int | None, now: datetime | None = None) -> bool:
    """Return whether the current hour-of-day falls in [start, end).

    Wrap-around is supported: start=22, end=6 means 22:00–05:59. NULL on
    either side disables the gate.
    """
    if start is None or end is None or start == end:
        return False
    hour = (now or datetime.now(timezone.utc)).hour
    if start < end:
        return start <= hour < end
    # wrap-around (e.g. 22 → 6)
    return hour >= start or hour < end


async def _project_or_404(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    proj = await db.get(Project, pid)
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return proj


def _is_auto_triggered(run: Run, task: Task | None) -> bool:
    """A run is considered auto-triggered when the scheduler enqueued the
    task itself — i.e. it carries the `fix-loop` tag the scheduler stamps
    on follow-ups before calling client.enqueue_task()."""
    if task is None:
        return False
    return "fix-loop" in (task.tags or [])


def _eligible_statuses_for(proj: Project) -> list[str]:
    """Resolve the auto-run eligible-status list for a project.

    Newer projects store the list explicitly; older rows pre-migration
    fall back to the legacy hard-coded set.
    """
    raw = getattr(proj, "auto_run_eligible_statuses", None)
    if raw:
        return [str(s) for s in raw]
    return list(DEFAULT_ELIGIBLE_TASK_STATUSES)


def _allowed_connectors_for(proj: Project) -> list[str]:
    raw = getattr(proj, "auto_run_allowed_connectors", None)
    return [str(s) for s in raw] if raw else []


async def _walk_recent_runs(
    db: AsyncSession,
    pid: uuid.UUID,
    *,
    limit: int,
) -> list[tuple[Run, Task | None]]:
    rows = await db.execute(
        select(Run, Task)
        .join(Task, Task.id == Run.task_id, isouter=True)
        .where(Run.project_id == pid)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


def _runs_in_window(
    rows: list[tuple[Run, Task | None]],
    *,
    since: datetime,
    only_auto: bool = True,
) -> int:
    n = 0
    for run, task in rows:
        if run.created_at is None or run.created_at < since:
            continue
        if only_auto and not _is_auto_triggered(run, task):
            continue
        n += 1
    return n


def _active_auto_runs(rows: list[tuple[Run, Task | None]]) -> int:
    n = 0
    for run, task in rows:
        if run.state not in ACTIVE_RUN_STATES:
            continue
        if _is_auto_triggered(run, task):
            n += 1
    return n


@router.get("/projects/{pid}", response_model=AutoRunStatusOut)
async def get_autorun(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    recent_limit: int = 10,
):
    proj = await _project_or_404(db, pid, user)

    limit = max(1, min(recent_limit, 50))
    # Pull a wider slice than the panel renders so we can compute hour /
    # day / active counts off the same rows without a separate query.
    wide_rows = await _walk_recent_runs(db, pid, limit=200)
    recent_rows = wide_rows[:limit]

    recent: list[AutoRunRecentRun] = []
    for run, task in recent_rows:
        recent.append(
            AutoRunRecentRun(
                id=run.id,
                task_id=run.task_id,
                task_title=task.title if task is not None else None,
                state=run.state.value if isinstance(run.state, RunState) else str(run.state),
                kind=run.kind.value if hasattr(run.kind, "value") else str(run.kind),
                started_at=run.started_at,
                finished_at=run.finished_at,
                auto_triggered=_is_auto_triggered(run, task),
                created_at=run.created_at,
            )
        )

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_ago = now - timedelta(hours=1)
    runs_today = _runs_in_window(wide_rows, since=today_start)
    runs_last_hour = _runs_in_window(wide_rows, since=hour_ago)
    active_auto = _active_auto_runs(wide_rows)

    daily_cap = proj.auto_run_daily_cap
    hourly_cap = getattr(proj, "auto_run_hourly_cap", 0) or 0
    concurrency_cap = getattr(proj, "auto_run_concurrency_cap", 0) or 0
    daily_cap_remaining = None if daily_cap == 0 else max(0, daily_cap - runs_today)
    hourly_cap_remaining = None if hourly_cap == 0 else max(0, hourly_cap - runs_last_hour)
    concurrency_remaining = (
        None if concurrency_cap == 0 else max(0, concurrency_cap - active_auto)
    )

    eligible = _eligible_statuses_for(proj)

    return AutoRunStatusOut(
        project_id=proj.id,
        enabled=proj.auto_run_fix,
        max_fix_loops=proj.max_fix_loops,
        wall_clock_minutes_override=proj.wall_clock_minutes_override,
        default_connector_id=proj.default_connector_id,
        auto_run_quiet_hours_start=proj.auto_run_quiet_hours_start,
        auto_run_quiet_hours_end=proj.auto_run_quiet_hours_end,
        auto_run_daily_cap=daily_cap,
        auto_run_concurrency_cap=concurrency_cap,
        auto_run_hourly_cap=hourly_cap,
        auto_run_allowed_connectors=_allowed_connectors_for(proj),
        auto_run_eligible_statuses=eligible,
        eligible_task_statuses=eligible,
        in_quiet_hours=_in_quiet_hours(
            proj.auto_run_quiet_hours_start, proj.auto_run_quiet_hours_end
        ),
        runs_today=runs_today,
        runs_last_hour=runs_last_hour,
        active_auto_runs=active_auto,
        daily_cap_remaining=daily_cap_remaining,
        hourly_cap_remaining=hourly_cap_remaining,
        concurrency_remaining=concurrency_remaining,
        recent_runs=recent,
    )


def _normalise_eligible_statuses(value: list) -> list[str]:
    return [(s.value if hasattr(s, "value") else str(s)) for s in value]


@router.patch("/projects/{pid}", response_model=AutoRunStatusOut)
async def patch_autorun(
    pid: uuid.UUID,
    body: AutoRunConfigPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _project_or_404(db, pid, user)
    fields = body.model_dump(exclude_unset=True)

    # Cross-field validation: quiet-hours either both set or both null.
    quiet_start = fields.get("auto_run_quiet_hours_start", proj.auto_run_quiet_hours_start)
    quiet_end = fields.get("auto_run_quiet_hours_end", proj.auto_run_quiet_hours_end)
    if (quiet_start is None) != (quiet_end is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "quiet_hours_start and quiet_hours_end must both be set or both be null",
        )

    # Coerce enum lists to raw strings for the ARRAY(Text) columns.
    if "auto_run_eligible_statuses" in fields and fields["auto_run_eligible_statuses"] is not None:
        fields["auto_run_eligible_statuses"] = _normalise_eligible_statuses(
            fields["auto_run_eligible_statuses"]
        )
    if "auto_run_allowed_connectors" in fields and fields["auto_run_allowed_connectors"] is not None:
        fields["auto_run_allowed_connectors"] = [
            str(c) for c in fields["auto_run_allowed_connectors"]
        ]

    for k, v in fields.items():
        setattr(proj, k, v)
    await db.flush()
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project.autorun.update",
        target_kind="project",
        target_id=str(proj.id),
        payload=fields,
    )
    await db.commit()
    await db.refresh(proj)

    # Re-use the GET handler's projection so PATCH responses include the
    # same derived view (eligible statuses, in_quiet_hours, runs_today...)
    # the panel uses to update its UI in-place after a save.
    return await get_autorun(pid, user, db)


# --- global / org-wide defaults --------------------------------------------


async def _get_or_seed_defaults(db: AsyncSession) -> AutoRunDefaults:
    """Singleton-row helper. The 0010 migration seeds id=1, but be defensive
    in case the table was created out-of-band (e.g. tests, fresh devs)."""
    row = await db.get(AutoRunDefaults, 1)
    if row is None:
        row = AutoRunDefaults(id=1)
        db.add(row)
        await db.flush()
    return row


@router.get("/defaults", response_model=AutoRunDefaultsOut)
async def get_autorun_defaults(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    # Defaults are surfaced read-only to everyone (so per-project UIs can
    # pre-fill from them); only owners can mutate. The dedicated PATCH
    # below enforces that.
    row = await _get_or_seed_defaults(db)
    return row


@router.patch("/defaults", response_model=AutoRunDefaultsOut)
async def patch_autorun_defaults(
    body: AutoRunDefaultsPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    if user.role != Role.owner:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only owners can edit global auto-run defaults"
        )
    row = await _get_or_seed_defaults(db)

    fields = body.model_dump(exclude_unset=True)
    quiet_start = fields.get("quiet_hours_start", row.quiet_hours_start)
    quiet_end = fields.get("quiet_hours_end", row.quiet_hours_end)
    if (quiet_start is None) != (quiet_end is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "quiet_hours_start and quiet_hours_end must both be set or both be null",
        )

    if "eligible_statuses" in fields and fields["eligible_statuses"] is not None:
        fields["eligible_statuses"] = _normalise_eligible_statuses(fields["eligible_statuses"])
    if "allowed_connectors" in fields and fields["allowed_connectors"] is not None:
        fields["allowed_connectors"] = [str(c) for c in fields["allowed_connectors"]]

    for k, v in fields.items():
        setattr(row, k, v)
    await db.flush()
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="autorun.defaults.update",
        target_kind="autorun_defaults",
        target_id="1",
        payload=fields,
    )
    await db.commit()
    await db.refresh(row)
    return row
