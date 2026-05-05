"""Task CRUD scoped under a project + a task-level run launcher."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import RunOut, TaskIn, TaskOut, TaskPatch
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, Run, Task, TaskStatus, User
from daedalus.git_status import needs_pull as git_needs_pull
from daedalus.hermes.client import HermesClient

router = APIRouter()


async def _project(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    res = await db.execute(select(Project).where(Project.id == pid))
    proj = res.scalar_one_or_none()
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return proj


async def _ensure_not_behind(project: Project, force: bool) -> None:
    """Block enqueue when the workspace is behind its upstream — running
    agents against a stale tree silently produces conflicts and outdated
    diffs. Operators can override with `?force=true`.

    Uses the cached git-status (no fresh `git fetch`); the project page
    refreshes the cache on mount so the gate keeps up with reality.
    """
    if force:
        return
    behind, status_obj = await git_needs_pull(str(project.id), project.workspace_path)
    if not behind:
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "kind": "git_pull_required",
            "behind_count": status_obj.behind_count,
            "branch": status_obj.branch,
            "upstream": status_obj.upstream,
            "message": (
                f"Project workspace is {status_obj.behind_count} commit"
                f"{'s' if status_obj.behind_count != 1 else ''} behind "
                f"{status_obj.upstream or 'upstream'}. "
                "Run `git pull` before launching agents (or pass force=true)."
            ),
        },
    )


@router.get("/projects/{pid}/tasks", response_model=list[TaskOut])
async def list_tasks(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    res = await db.execute(
        select(Task).where(Task.project_id == pid).order_by(Task.priority.asc(), Task.created_at.asc())
    )
    return res.scalars().all()


@router.post("/projects/{pid}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    pid: uuid.UUID,
    body: TaskIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _project(db, pid, user)
    task = Task(
        project_id=proj.id,
        title=body.title,
        description=body.description,
        acceptance_criteria=body.acceptance_criteria,
        priority=body.priority,
        connector_id=body.connector_id or proj.default_connector_id,
        profile=body.profile,
        depends_on=body.depends_on,
        tags=body.tags,
        estimated_minutes=body.estimated_minutes,
    )
    db.add(task)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="task.create", target_kind="task", target_id=str(task.id),
        payload={"project_id": str(pid), "title": task.title},
    )
    await db.commit()
    return task


@router.patch("/tasks/{tid}", response_model=TaskOut)
async def patch_task(
    tid: uuid.UUID,
    body: TaskPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    res = await db.execute(select(Task).where(Task.id == tid))
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    await _project(db, task.project_id, user)
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(task, k, v)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="task.update", target_kind="task", target_id=str(task.id),
        payload=fields,
    )
    await db.commit()
    return task


@router.delete("/tasks/{tid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    tid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    res = await db.execute(select(Task).where(Task.id == tid))
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    await _project(db, task.project_id, user)
    await db.delete(task)
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="task.delete", target_kind="task", target_id=str(tid),
    )
    await db.commit()


@router.post(
    "/projects/{pid}/run-all",
    response_model=list[RunOut],
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_all_tasks(
    pid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    force: bool = False,
):
    """Bulk-enqueue every backlog / ready / needs_fixes task on the project.

    Hermes's per-project lease + DAG dependency resolution make this safe
    for arbitrarily many tasks: only one runs per project at a time,
    dependents wait for their `depends_on` entries to finish.

    Eligible statuses:
      * ``backlog`` / ``ready`` — fresh work
      * ``needs_fixes`` — Argus rejected the previous attempt; re-run picks
        up where the agent left off (matches the per-task ▶ Run button).
        The project's ``auto_run_fix`` toggle only controls *automatic*
        re-queue after Argus fails, not what bulk Run-all picks up.

    Skipped statuses:
      * ``in_progress`` / ``verifying`` — already active
      * ``done`` / ``cancelled`` — terminal
    """
    project = await _project(db, pid, user)
    await _ensure_not_behind(project, force)
    res = await db.execute(
        select(Task)
        .where(
            Task.project_id == pid,
            Task.status.in_(
                [TaskStatus.backlog, TaskStatus.ready, TaskStatus.needs_fixes]
            ),
        )
        .order_by(Task.priority.asc(), Task.created_at.asc())
    )
    eligible = res.scalars().all()
    if not eligible:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no eligible tasks (need at least one in backlog / ready / needs_fixes)",
        )

    client = HermesClient(db)
    enqueued: list[Run] = []
    skipped: list[dict[str, str]] = []
    for task in eligible:
        try:
            run = await client.enqueue_task(task)
            enqueued.append(run)
        except ValueError as exc:
            skipped.append({"task_id": str(task.id), "reason": str(exc)})

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project.run_all",
        target_kind="project",
        target_id=str(pid),
        payload={
            "enqueued_count": len(enqueued),
            "skipped_count": len(skipped),
            "skipped": skipped,
        },
    )
    await db.commit()
    for run in enqueued:
        await db.refresh(run)
    return enqueued


@router.post("/tasks/{tid}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def run_task(
    tid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    force: bool = False,
):
    res = await db.execute(select(Task).where(Task.id == tid))
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    project = await _project(db, task.project_id, user)
    await _ensure_not_behind(project, force)
    if task.status in (TaskStatus.in_progress, TaskStatus.verifying):
        raise HTTPException(status.HTTP_409_CONFLICT, "task is already running")

    task.status = TaskStatus.ready
    await db.flush()
    try:
        run = await HermesClient(db).enqueue_task(task)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="task.enqueue", target_kind="task", target_id=str(task.id),
        payload={"run_id": str(run.id)},
    )
    await db.commit()
    return run
