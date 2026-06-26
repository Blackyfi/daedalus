"""Run lifecycle endpoints: pause/resume/interrupt/kill/detach/inject + transcript fetch."""
from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import ArgusOut, InjectIn, ResizeIn, RunOut, SnapshotOut
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import ArgusReport, Project, Role, Run, RunKind, Snapshot, Task, User
from daedalus.git_status import needs_pull as git_needs_pull
from daedalus.hermes.client import HermesClient
from daedalus.storage.objects import get_object_store

router = APIRouter()


async def _run_for(user: User, db: AsyncSession, rid: uuid.UUID) -> Run:
    res = await db.execute(
        select(Run, Project).join(Project, Project.id == Run.project_id).where(Run.id == rid)
    )
    row = res.first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    run, proj = row
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your run")
    return run


@router.get("/{rid}", response_model=RunOut)
async def get_run(rid: uuid.UUID, user=Depends(current_user), db=Depends(get_session)):
    return await _run_for(user, db, rid)


@router.get("/projects/{pid}", response_model=list[RunOut])
async def list_runs_for_project(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    project_res = await db.execute(select(Project).where(Project.id == pid))
    project = project_res.scalar_one_or_none()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and project.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    res = await db.execute(
        select(Run)
        .where(Run.project_id == pid)
        .order_by(Run.created_at.desc())
        .limit(min(max(limit, 1), 200))
    )
    return res.scalars().all()


def _make_lifecycle(action: Literal["pause", "resume", "interrupt", "kill", "detach"]):
    async def _ep(
        rid: uuid.UUID,
        request: Request,
        user: Annotated[User, Depends(current_user)],
        db: AsyncSession = Depends(get_session),
    ):
        run = await _run_for(user, db, rid)
        await HermesClient(db).send_signal(run, action)
        await record(
            db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
            action=f"run.{action}", target_kind="run", target_id=str(rid),
        )
        await db.commit()
        return {"status": "ok"}
    return _ep


router.add_api_route("/{rid}/pause",     _make_lifecycle("pause"),     methods=["POST"])
router.add_api_route("/{rid}/resume",    _make_lifecycle("resume"),    methods=["POST"])
router.add_api_route("/{rid}/interrupt", _make_lifecycle("interrupt"), methods=["POST"])
router.add_api_route("/{rid}/kill",      _make_lifecycle("kill"),      methods=["POST"])
router.add_api_route("/{rid}/detach",    _make_lifecycle("detach"),    methods=["POST"])


@router.post("/{rid}/inject")
async def inject(
    rid: uuid.UUID,
    body: InjectIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    run = await _run_for(user, db, rid)
    await HermesClient(db).inject_text(run, body.text)
    # log keystrokes per §10.7
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="run.inject", target_kind="run", target_id=str(rid),
        payload={"len": len(body.text)},
    )
    await db.commit()
    return {"status": "ok"}


@router.post("/{rid}/resize")
async def resize(
    rid: uuid.UUID,
    body: ResizeIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    run = await _run_for(user, db, rid)
    await HermesClient(db).resize_pty(run, rows=body.rows, cols=body.cols)
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="run.resize", target_kind="run", target_id=str(rid),
        payload={"rows": body.rows, "cols": body.cols},
    )
    await db.commit()
    return {"status": "ok"}


@router.get("/{rid}/transcript")
async def transcript(
    rid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    run = await _run_for(user, db, rid)
    if run.transcript_object_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transcript not yet available")
    return {"object_key": run.transcript_object_key}


@router.get("/{rid}/transcript/text", response_class=PlainTextResponse)
async def transcript_text(
    rid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    run = await _run_for(user, db, rid)
    if run.transcript_object_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transcript not yet available")
    return get_object_store().get_text(run.transcript_object_key)


@router.get("/{rid}/diff", response_class=PlainTextResponse)
async def diff_text(
    rid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    """Unified `git diff <default_branch>...HEAD` for the run's worktree.

    Cached in object storage when present (``Run.diff_object_key``); otherwise
    computed on demand from the worktree, since per-run worktrees aren't
    cleaned up automatically.
    """
    run = await _run_for(user, db, rid)
    if run.diff_object_key:
        try:
            return get_object_store().get_text(run.diff_object_key)
        except Exception:
            # Cached object missing/unreadable — fall through and recompute the
            # diff from the worktree below.
            pass

    if not run.worktree_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no worktree to diff")

    project = await db.get(Project, run.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project missing")

    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        f"{project.git_default_branch}...HEAD",
        "--no-color",
        cwd=run.worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode not in (0, None):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"git diff failed: {err.decode().strip()}",
        )
    return out.decode("utf-8", errors="replace")


@router.get("/{rid}/snapshot", response_model=SnapshotOut | None)
async def get_snapshot(
    rid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    """Return the pre-yolo snapshot for this run, or ``null`` if none exists.

    Returning 200 + ``null`` (rather than 404) for the legitimate
    "no-snapshot" case keeps the SPA's run panel from polluting the
    browser console with red 404 lines for every non-yolo or queued
    task run that the auto-refetch picks up.
    """
    await _run_for(user, db, rid)
    res = await db.execute(select(Snapshot).where(Snapshot.run_id == rid))
    return res.scalar_one_or_none()


@router.post("/{rid}/rollback", status_code=status.HTTP_200_OK)
async def rollback_to_snapshot(
    rid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    """Reset the run's worktree to its pre-yolo snapshot tag.

    Refuses to roll back a run that is still active — pause/kill it first.
    """
    run = await _run_for(user, db, rid)
    if run.state.value in ("queued", "claimed", "running"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot rollback an active run; pause or kill it first",
        )

    snap_res = await db.execute(select(Snapshot).where(Snapshot.run_id == rid))
    snap = snap_res.scalar_one_or_none()
    if snap is None or not snap.git_tag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no snapshot for this run")
    if not run.worktree_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "run has no worktree to roll back")

    proc = await asyncio.create_subprocess_exec(
        "git", "reset", "--hard", snap.git_tag,
        cwd=run.worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode not in (0, None):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"git reset failed: {stderr.decode().strip()}",
        )
    proc = await asyncio.create_subprocess_exec(
        "git", "clean", "-fdx",
        cwd=run.worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="run.rollback",
        target_kind="run",
        target_id=str(rid),
        payload={"git_tag": snap.git_tag},
    )
    await db.commit()
    return {"status": "ok", "git_tag": snap.git_tag}


_RETRYABLE_STATES = {"failed", "cancelled", "aborted_unsafe"}


@router.post("/{rid}/retry", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def retry_run(
    rid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    force: bool = False,
):
    """Clone a failed run as a fresh queued run.

    Same `kind`, `task_id`, `project_id`, `lane`, and `connector_snapshot`
    as the original. The new row's ``retry_of`` points back at the source
    so the SPA can render a retry chain.

    Refuses to retry runs that are still active (queued/claimed/running)
    or that already succeeded — a successful run doesn't need a retry,
    and an in-flight run should be killed first.
    """
    run = await _run_for(user, db, rid)
    if run.state.value not in _RETRYABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"can only retry failed/cancelled/aborted_unsafe runs (got {run.state.value})",
        )

    # Same git-pull guard as fresh enqueues — agents shouldn't retry against
    # a stale tree.
    project = await db.get(Project, run.project_id)
    if project is not None and not force:
        behind, status_obj = await git_needs_pull(str(project.id), project.workspace_path)
        if behind:
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
                        "Run `git pull` before retrying (or pass force=true)."
                    ),
                },
            )

    # Re-attempt task runs only if the underlying task still exists. For
    # task-bound retries we also flip the task back to `ready` so the
    # board reflects the pending state.
    task: Task | None = None
    if run.task_id is not None:
        task_res = await db.execute(select(Task).where(Task.id == run.task_id))
        task = task_res.scalar_one_or_none()
        if task is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "original run's task no longer exists",
            )

    client = HermesClient(db)
    new_run = Run(
        task_id=run.task_id,
        project_id=run.project_id,
        kind=run.kind,
        lane=run.lane,
        connector_snapshot=run.connector_snapshot,
        retry_of=run.id,
    )
    new_run = await client._create_run(run=new_run)

    if task is not None and run.kind.value == "task":
        from daedalus.db.models import TaskStatus

        task.status = TaskStatus.ready

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="run.retry",
        target_kind="run",
        target_id=str(new_run.id),
        payload={
            "retry_of": str(run.id),
            "kind": run.kind.value,
            "previous_state": run.state.value,
            "previous_exit_code": run.exit_code,
        },
    )
    await db.commit()
    await db.refresh(new_run)
    return new_run


@router.get("/{rid}/argus", response_model=ArgusOut)
async def argus_report(
    rid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _run_for(user, db, rid)
    # `rid` may be the argus run id (direct) or the task run id (what the SPA
    # holds). Try the direct link first, then resolve via the argus run whose
    # source_run_id points back at this task run.
    res = await db.execute(select(ArgusReport).where(ArgusReport.run_id == rid))
    rpt = res.scalar_one_or_none()
    if not rpt:
        argus_run = (
            await db.execute(
                select(Run.id).where(
                    Run.source_run_id == rid, Run.kind == RunKind.argus
                )
            )
        ).scalar_one_or_none()
        if argus_run is not None:
            rpt = (
                await db.execute(
                    select(ArgusReport).where(ArgusReport.run_id == argus_run)
                )
            ).scalar_one_or_none()
    if not rpt:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return rpt
