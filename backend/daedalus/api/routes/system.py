"""System-level endpoints: Pythia subscription cache + per-project runner state.

Both are read-only and cheap; both are polled by the SPA's Shell header
runner bar (project-plan.md §8.5.1).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth.dependencies import current_user
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, Run, RunState, Task, User
from daedalus.db.redis import get_redis
from daedalus.hermes.leases import (
    ACTIVE_PROJECTS_KEY,
    project_lease_key,
)
from daedalus.pythia.probe import (
    read_cached_async,
)

router = APIRouter()


@router.get("/subscription")
async def get_subscription(
    user: Annotated[User, Depends(current_user)],
) -> dict[str, Any]:
    """Cached Pythia snapshot (Talos refreshes every PYTHIA_REFRESH_SECONDS).

    Returns 200 + ``{kind: "stale_or_missing"}`` if no probe has landed yet —
    surfacing the fallback explicitly is friendlier than 404 for a feature
    that may not have run yet on a freshly booted host.

    Requires auth — the snapshot includes the operator's email + plan tier
    and shouldn't be readable by anyone who can hit the box.
    """
    del user  # auth gate only
    redis = get_redis()
    info = await read_cached_async(redis)
    if info is None:
        return {
            "kind": "stale_or_missing",
            "raw_text": "",
            "fetched_at": None,
            "plan": None,
            "plan_tier": None,
            "weekly_used_pct": None,
            "five_hour_used_pct": None,
            "weekly_resets_in": None,
            "five_hour_resets_in": None,
            "email": None,
            "error": None,
        }
    return asdict(info)


@router.get("/runners")
async def get_runners(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Per-project runner snapshot for the global runner bar.

    Returns:
      - max_concurrent_projects: the configured ceiling
      - active: list of {project_id, project_name, run_id, task_title, started_at}
      - active_count: len(active)
    """
    settings = get_settings()
    redis = get_redis()

    # Pull the active set + every lease value in one round-trip.
    raw_members = await redis.smembers(ACTIVE_PROJECTS_KEY)
    project_ids: list[str] = []
    for m in raw_members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", errors="replace")
        project_ids.append(m)

    active: list[dict[str, Any]] = []
    if project_ids:
        # Authorisation: members only see their own projects unless owner.
        if user.role == Role.owner:
            visible_ids = project_ids
        else:
            res = await db.execute(
                select(Project.id).where(
                    Project.id.in_([uuid.UUID(p) for p in project_ids if _is_uuid(p)]),
                    Project.owner_id == user.id,
                )
            )
            visible_ids = [str(pid) for (pid,) in res.all()]

        if visible_ids:
            run_ids: list[str] = []
            for pid in visible_ids:
                val = await redis.get(project_lease_key(pid))
                if val is None:
                    continue
                if isinstance(val, bytes):
                    val = val.decode("utf-8", errors="replace")
                run_ids.append(val)

            if run_ids:
                runs_res = await db.execute(
                    select(Run, Project, Task)
                    .join(Project, Project.id == Run.project_id)
                    .join(Task, Task.id == Run.task_id, isouter=True)
                    .where(Run.id.in_([uuid.UUID(r) for r in run_ids if _is_uuid(r)]))
                )
                for run, project, task in runs_res.all():
                    if run.state not in (RunState.running, RunState.claimed):
                        continue
                    active.append(
                        {
                            "project_id": str(project.id),
                            "project_name": project.name,
                            "run_id": str(run.id),
                            "run_kind": run.kind.value,
                            "task_title": task.title if task else None,
                            "started_at": run.started_at.isoformat() if run.started_at else None,
                        }
                    )

    return {
        "max_concurrent_projects": settings.max_concurrent_projects,
        "active_count": len(active),
        "active": active,
    }


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
