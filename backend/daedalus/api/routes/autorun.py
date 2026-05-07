"""Auto-run configuration endpoints — backs the AutoRun panel.

These routes thin-wrap the relevant subset of the project record (toggle,
caps, default connector, quiet hours, daily cap) and add two derived bits
the panel needs that the bare project resource doesn't surface:

  * the task statuses that auto-run will pick up, so the panel can render
    the eligibility list without hard-coding scheduler internals
  * a recent-runs list with an `auto_triggered` flag — runs that the
    scheduler enqueued itself rather than ones the user clicked
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import (
    AutoRunConfigPatch,
    AutoRunRecentRun,
    AutoRunStatusOut,
)
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, Run, RunState, Task, TaskStatus, User

router = APIRouter()


# Statuses the run-all / scheduler treats as eligible for auto-run.
# Mirrors the filter in ProjectPage.confirmRunAll + scheduler._auto_enqueue_fix.
ELIGIBLE_TASK_STATUSES: tuple[str, ...] = (
    TaskStatus.backlog.value,
    TaskStatus.ready.value,
    TaskStatus.needs_fixes.value,
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


async def _runs_today_count(
    db: AsyncSession, project_id: uuid.UUID, since: datetime
) -> int:
    """Count auto-triggered runs the project has racked up since `since`.

    Joins runs to their parent task and filters to the `fix-loop` tag the
    scheduler stamps on each fix follow-up. Cancelled runs still count
    against the cap so a runaway tag-loop can't sneak past it.
    """
    rows = await db.execute(
        select(Run, Task)
        .join(Task, Task.id == Run.task_id)
        .where(Run.project_id == project_id)
        .where(Run.created_at >= since)
    )
    n = 0
    for run, task in rows.all():
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

    # Recent runs (any kind) the panel renders — tagged so the UI can
    # distinguish "auto" from "manual" without re-deriving the rule.
    limit = max(1, min(recent_limit, 50))
    rows = await db.execute(
        select(Run, Task)
        .join(Task, Task.id == Run.task_id, isouter=True)
        .where(Run.project_id == pid)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    recent: list[AutoRunRecentRun] = []
    for run, task in rows.all():
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

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    runs_today = await _runs_today_count(db, pid, today_start)
    daily_cap_remaining: int | None
    if proj.auto_run_daily_cap == 0:
        daily_cap_remaining = None  # unlimited
    else:
        daily_cap_remaining = max(0, proj.auto_run_daily_cap - runs_today)

    return AutoRunStatusOut(
        project_id=proj.id,
        enabled=proj.auto_run_fix,
        max_fix_loops=proj.max_fix_loops,
        wall_clock_minutes_override=proj.wall_clock_minutes_override,
        default_connector_id=proj.default_connector_id,
        auto_run_quiet_hours_start=proj.auto_run_quiet_hours_start,
        auto_run_quiet_hours_end=proj.auto_run_quiet_hours_end,
        auto_run_daily_cap=proj.auto_run_daily_cap,
        eligible_task_statuses=list(ELIGIBLE_TASK_STATUSES),
        in_quiet_hours=_in_quiet_hours(
            proj.auto_run_quiet_hours_start, proj.auto_run_quiet_hours_end
        ),
        runs_today=runs_today,
        daily_cap_remaining=daily_cap_remaining,
        recent_runs=recent,
    )


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
