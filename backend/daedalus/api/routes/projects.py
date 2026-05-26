"""Project CRUD."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import ProjectIn, ProjectOut, ProjectPatch
from daedalus.auth.audit import record
from daedalus.costs import month_start
from daedalus.auth.dependencies import current_user
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, Run, Task, TaskStatus, User
from daedalus.db.redis import get_redis
from daedalus.git_status import GitStatus, get_status as get_git_status

router = APIRouter()


async def _attach_rate_limit_pause(projects: Iterable[Project]) -> None:
    """Surface per-connector rate-limit pause state from Redis onto each
    Project instance as ad-hoc Python attributes. Pydantic's from_attributes
    serialization picks them up into ProjectOut.

    Cheap: one MGET for all connectors used. Falls through to None on any
    Redis hiccup so the project endpoint still works.
    """
    project_list = list(projects)
    if not project_list:
        return
    connectors = {
        p.default_connector_id for p in project_list if p.default_connector_id
    }
    if not connectors:
        for p in project_list:
            p.rate_limit_paused_until = None
            p.rate_limit_paused_reason = None
        return
    keys = [f"daedalus:connector_paused:{cid}" for cid in connectors]
    try:
        redis = get_redis()
        raw_values = await redis.mget(*keys)
    except Exception:
        for p in project_list:
            p.rate_limit_paused_until = None
            p.rate_limit_paused_reason = None
        return
    state: dict[str, tuple[datetime | None, str | None]] = {}
    for cid, raw in zip(connectors, raw_values):
        if not raw:
            state[cid] = (None, None)
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            retry_after_iso = payload.get("retry_after")
            retry_after = (
                datetime.fromisoformat(retry_after_iso) if retry_after_iso else None
            )
            reason = (
                f"Claude rate limit hit at {payload.get('hit_at')} — "
                f"resumes {retry_after_iso}"
            )
        except Exception:
            retry_after = None
            reason = "Claude rate limit (parse error)"
        state[cid] = (retry_after, reason)
    for p in project_list:
        ra, reason = state.get(p.default_connector_id or "", (None, None))
        p.rate_limit_paused_until = ra
        p.rate_limit_paused_reason = reason


def _canonicalize_workspace(path: str) -> str:
    """Resolve the path and ensure it lives inside the configured workspaces root."""
    root = os.path.realpath(get_settings().workspaces_root)
    abs_ = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if not abs_.startswith(root + os.sep) and abs_ != root:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace_path must be inside the workspaces root")
    return abs_


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Project)
    if user.role != Role.owner:
        stmt = stmt.where(Project.owner_id == user.id)
    res = await db.execute(stmt.order_by(Project.created_at.desc()))
    projects = list(res.scalars().all())
    await _attach_rate_limit_pause(projects)
    return projects


@router.get("/stats")
async def project_stats(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    """Per-project task counts grouped by status, plus a `last_activity_at`
    timestamp. The SPA's project list uses this to render KPI badges and
    a "since you last opened" delta (snapshot is kept in localStorage,
    so no per-user state lives in the DB).

    Returns shape: {"<project_id>": {by_status, total, last_activity_at}}.
    """
    proj_stmt = select(Project.id)
    if user.role != Role.owner:
        proj_stmt = proj_stmt.where(Project.owner_id == user.id)
    visible_ids = [pid for (pid,) in (await db.execute(proj_stmt)).all()]

    out: dict[str, dict[str, Any]] = {
        str(pid): {
            "by_status": {s.value: 0 for s in TaskStatus},
            "total": 0,
            "last_activity_at": None,
            "avg_cycle_seconds_7d": None,
            "completed_in_window_7d": 0,
            "cost_cap_usd_micros": None,
            "month_cost_usd_micros": 0,
        }
        for pid in visible_ids
    }

    if not visible_ids:
        return out

    # Monthly cost cap (config) + this calendar month's run spend per project,
    # so the dashboard can render a "spent / cap" budget badge.
    cap_rows = await db.execute(
        select(Project.id, Project.monthly_cost_cap_usd_micros).where(Project.id.in_(visible_ids))
    )
    for project_id, cap in cap_rows.all():
        out[str(project_id)]["cost_cap_usd_micros"] = int(cap) if cap is not None else None
    spend_rows = await db.execute(
        select(Run.project_id, func.coalesce(func.sum(Run.cost_usd_micros), 0))
        .where(Run.project_id.in_(visible_ids), Run.created_at >= month_start())
        .group_by(Run.project_id)
    )
    for project_id, spent in spend_rows.all():
        out[str(project_id)]["month_cost_usd_micros"] = int(spent or 0)

    # Counts grouped by (project, status).
    count_rows = await db.execute(
        select(Task.project_id, Task.status, func.count(Task.id))
        .where(Task.project_id.in_(visible_ids))
        .group_by(Task.project_id, Task.status)
    )
    for project_id, status_value, n in count_rows.all():
        bucket = out[str(project_id)]
        # SQLAlchemy returns the enum value already mapped to TaskStatus.
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        bucket["by_status"][key] = int(n)
        bucket["total"] += int(n)

    # Latest task touch per project — gives the dashboard a "last activity"
    # subtitle without us having to scan runs.
    last_rows = await db.execute(
        select(Task.project_id, func.max(Task.updated_at))
        .where(Task.project_id.in_(visible_ids))
        .group_by(Task.project_id)
    )
    for project_id, last_at in last_rows.all():
        bucket = out[str(project_id)]
        bucket["last_activity_at"] = last_at.isoformat() if last_at is not None else None

    # 7-day rolling avg cycle time per task = max(run.finished_at) - min(run.started_at)
    # over runs attached to that task, averaged across tasks that landed in
    # `done` within the last 7 days. Empty windows stay None so the SPA can
    # render N/A without doing the math itself.
    since = datetime.now(timezone.utc) - timedelta(days=7)
    per_task_dur = (
        select(
            Task.project_id.label("project_id"),
            (
                func.extract(
                    "epoch",
                    func.max(Run.finished_at) - func.min(Run.started_at),
                )
            ).label("dur_seconds"),
        )
        .join(Run, Run.task_id == Task.id)
        .where(
            Task.project_id.in_(visible_ids),
            Task.status == TaskStatus.done,
            Task.updated_at >= since,
            Run.started_at.is_not(None),
            Run.finished_at.is_not(None),
        )
        .group_by(Task.project_id, Task.id)
        .subquery()
    )
    avg_rows = await db.execute(
        select(
            per_task_dur.c.project_id,
            func.avg(per_task_dur.c.dur_seconds),
            func.count(per_task_dur.c.dur_seconds),
        )
        .where(per_task_dur.c.dur_seconds > 0)
        .group_by(per_task_dur.c.project_id)
    )
    for project_id, avg_seconds, sample_count in avg_rows.all():
        bucket = out[str(project_id)]
        bucket["avg_cycle_seconds_7d"] = (
            float(avg_seconds) if avg_seconds is not None else None
        )
        bucket["completed_in_window_7d"] = int(sample_count or 0)

    return out


# ── Git status (drives the "pull required" guard) ───────────────────────────


def _git_status_payload(status: GitStatus) -> dict[str, Any]:
    from dataclasses import asdict
    return {**asdict(status), "needs_pull": status.needs_pull()}


@router.get("/git-status")
async def bulk_git_status(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    """Cached git-status for every visible project.

    Uses the cache only — does NOT trigger a fresh `git fetch`. The
    project page's mount fires `?refresh=true` on the per-project endpoint
    so the cache stays warm without polling-load on shared remotes.
    """
    proj_stmt = select(Project)
    if user.role != Role.owner:
        proj_stmt = proj_stmt.where(Project.owner_id == user.id)
    rows = (await db.execute(proj_stmt)).scalars().all()

    out: dict[str, dict[str, Any]] = {}
    for proj in rows:
        try:
            status = await get_git_status(str(proj.id), proj.workspace_path, refresh=False)
        except Exception:
            status = GitStatus(error="status probe failed")
        out[str(proj.id)] = _git_status_payload(status)
    return out


@router.get("/{pid}/git-status")
async def project_git_status(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    refresh: bool = False,
) -> dict[str, Any]:
    """Per-project git status. With `?refresh=true` we run `git fetch`."""
    proj = await db.get(Project, pid)
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    git = await get_git_status(str(pid), proj.workspace_path, refresh=refresh)
    return _git_status_payload(git)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    workspace = _canonicalize_workspace(body.workspace_path)
    proj = Project(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        workspace_path=workspace,
        git_default_branch=body.git_default_branch,
        default_connector_id=body.default_connector_id,
        max_fix_loops=body.max_fix_loops,
        auto_run_fix=body.auto_run_fix,
        planning_model=body.planning_model,
        task_model=body.task_model,
        verifier_model=body.verifier_model,
        argus_enabled=body.argus_enabled,
        wall_clock_minutes_override=body.wall_clock_minutes_override,
        monthly_cost_cap_usd_micros=body.monthly_cost_cap_usd_micros,
    )
    db.add(proj)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="project.create", target_kind="project", target_id=str(proj.id),
        payload={"name": proj.name},
    )
    await db.commit()
    return proj


async def _get_project_or_404(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    res = await db.execute(select(Project).where(Project.id == pid))
    proj = res.scalar_one_or_none()
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return proj


@router.get("/{pid}", response_model=ProjectOut)
async def get_project(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _get_project_or_404(db, pid, user)
    await _attach_rate_limit_pause([proj])
    return proj


@router.patch("/{pid}", response_model=ProjectOut)
async def patch_project(
    pid: uuid.UUID,
    body: ProjectPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _get_project_or_404(db, pid, user)
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(proj, k, v)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="project.update", target_kind="project", target_id=str(proj.id),
        payload=fields,
    )
    await db.commit()
    # `updated_at` has onupdate=func.now() — Postgres assigns the value, so
    # the in-memory instance is stale post-commit. Without this refresh, the
    # ProjectOut serializer triggers a lazy-load and crashes with MissingGreenlet.
    await db.refresh(proj)
    return proj


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    pid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _get_project_or_404(db, pid, user)
    await db.delete(proj)
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="project.delete", target_kind="project", target_id=str(pid),
    )
    await db.commit()
