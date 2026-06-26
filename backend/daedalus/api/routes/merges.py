"""Batch-merge endpoints.

  POST /projects/{pid}/merge-batch/preview          — categorize candidates
  POST /projects/{pid}/merge-batch                  — create + persist a batch
  GET  /projects/{pid}/merge-batches                — list batches for project
  GET  /projects/{pid}/merge-batches/{bid}          — fetch batch + items
  POST /projects/{pid}/merge-batches/{bid}/resolve  — queue agent for the next
                                                       conflict (sequential)
  POST /projects/{pid}/merge-batches/{bid}/ship     — fast-forward main to the
                                                       integration tip
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth.audit import record as audit_record
from daedalus.auth.dependencies import current_user
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import (
    Connector,
    MergeBatch,
    MergeBatchItem,
    Project,
    Role,
    User,
)
from daedalus.merge import (
    execute_batch,
    plan_batch,
    reconcile_resolution_states,
    resolve_next_conflict,
    select_candidates,
    ship_batch,
    undo_ship,
)

router = APIRouter()


# ── schemas ─────────────────────────────────────────────────────────────


class MergePreviewIn(BaseModel):
    task_ids: list[uuid.UUID] | None = None
    require_argus_pass: bool = True


class BranchPlanOut(BaseModel):
    task_id: uuid.UUID
    task_title: str
    run_id: uuid.UUID | None
    branch: str
    argus_verdict: str | None
    category: str
    conflicting_files: list[str] = Field(default_factory=list)
    commits_ahead: int = 0
    files_changed: int = 0


class MergePreviewOut(BaseModel):
    project_id: uuid.UUID
    project_name: str
    workspace_path: str
    default_branch: str
    proposed_integration_branch: str
    plans: list[BranchPlanOut]


class MergeBatchIn(MergePreviewIn):
    pass


class MergeItemOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    branch: str
    category: str
    state: str
    conflicting_files: list[str]
    commits_ahead: int
    files_changed: int
    error: str | None
    resolution_task_id: uuid.UUID | None
    resolution_run_id: uuid.UUID | None


class MergeBatchOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    integration_branch: str
    integration_worktree: str
    state: str
    verify_exit_code: int | None
    verify_output: str | None
    error: str | None
    require_argus_pass: bool
    created_at: str
    updated_at: str
    shipped_at: str | None
    items: list[MergeItemOut]
    counts: dict[str, int]


class ResolutionStepOut(BaseModel):
    item_id: uuid.UUID
    branch: str
    state: str
    task_id: uuid.UUID | None
    run_id: uuid.UUID | None
    error: str | None


class ShipIn(BaseModel):
    delete_source_branches: bool = True
    remove_worktree: bool = True


class ShipResultOut(BaseModel):
    state: str
    integration_branch: str
    default_branch: str
    pruned_branches: list[str]
    removed_worktree: bool
    error: str | None


# ── helpers ─────────────────────────────────────────────────────────────


def _to_plan_out(plan) -> BranchPlanOut:
    return BranchPlanOut(
        task_id=plan.candidate.task_id,
        task_title=plan.candidate.task_title,
        run_id=plan.candidate.run_id,
        branch=plan.candidate.branch,
        argus_verdict=plan.candidate.argus_verdict,
        category=plan.category,
        conflicting_files=list(plan.conflicting_files),
        commits_ahead=plan.commits_ahead,
        files_changed=plan.files_changed,
    )


def _item_to_out(item: MergeBatchItem) -> MergeItemOut:
    return MergeItemOut(
        id=item.id,
        task_id=item.task_id,
        branch=item.branch,
        category=item.category.value,
        state=item.state.value,
        conflicting_files=list(item.conflicting_files or []),
        commits_ahead=item.commits_ahead,
        files_changed=item.files_changed,
        error=item.error,
        resolution_task_id=item.resolution_task_id,
        resolution_run_id=item.resolution_run_id,
    )


def _batch_to_out(batch: MergeBatch, items: list[MergeBatchItem]) -> MergeBatchOut:
    counts: dict[str, int] = {}
    for it in items:
        counts[it.state.value] = counts.get(it.state.value, 0) + 1
    return MergeBatchOut(
        id=batch.id,
        project_id=batch.project_id,
        integration_branch=batch.integration_branch,
        integration_worktree=batch.integration_worktree,
        state=batch.state.value,
        verify_exit_code=batch.verify_exit_code,
        verify_output=batch.verify_output,
        error=batch.error,
        require_argus_pass=batch.require_argus_pass,
        created_at=batch.created_at.isoformat(),
        updated_at=batch.updated_at.isoformat(),
        shipped_at=batch.shipped_at.isoformat() if batch.shipped_at else None,
        items=[_item_to_out(i) for i in items],
        counts=counts,
    )


async def _load_project(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    res = await db.execute(select(Project).where(Project.id == pid))
    project = res.scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and project.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return project


async def _load_batch(
    db: AsyncSession, pid: uuid.UUID, bid: uuid.UUID, user: User
) -> tuple[MergeBatch, list[MergeBatchItem]]:
    # Ownership gate first (also 404s an unknown project), then the batch.
    await _load_project(db, pid, user)
    batch = await db.get(MergeBatch, bid)
    if batch is None or batch.project_id != pid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "merge batch not found")
    items_res = await db.execute(
        select(MergeBatchItem)
        .where(MergeBatchItem.batch_id == bid)
        .order_by(MergeBatchItem.created_at.asc())
    )
    items = list(items_res.scalars())
    return (batch, items)


async def _verify_commands_for(db: AsyncSession, project: Project) -> list[str]:
    if not project.default_connector_id:
        return []
    res = await db.execute(
        select(Connector).where(Connector.connector_id == project.default_connector_id)
    )
    connector = res.scalar_one_or_none()
    if connector is None:
        return []
    spec = connector.spec or {}
    cmds = spec.get("verify_commands") or []
    return [str(c) for c in cmds if isinstance(c, str)]


# ── routes ──────────────────────────────────────────────────────────────


@router.post("/{pid}/merge-batch/preview", response_model=MergePreviewOut)
async def merge_batch_preview(
    pid: uuid.UUID,
    body: MergePreviewIn,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> MergePreviewOut:
    project = await _load_project(db, pid, user)
    candidates = await select_candidates(
        db,
        project_id=project.id,
        require_argus_pass=body.require_argus_pass,
        only_task_ids=body.task_ids,
    )
    integration_branch = f"daedalus-merge-{uuid.uuid4()}"
    plans = await plan_batch(
        workspace_path=project.workspace_path,
        default_branch=project.git_default_branch,
        candidates=candidates,
        integration_branch=integration_branch,
    )
    return MergePreviewOut(
        project_id=project.id,
        project_name=project.name,
        workspace_path=project.workspace_path,
        default_branch=project.git_default_branch,
        proposed_integration_branch=integration_branch,
        plans=[_to_plan_out(p) for p in plans],
    )


@router.post("/{pid}/merge-batch", response_model=MergeBatchOut)
async def merge_batch_create(
    pid: uuid.UUID,
    body: MergeBatchIn,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> MergeBatchOut:
    project = await _load_project(db, pid, user)
    candidates = await select_candidates(
        db,
        project_id=project.id,
        require_argus_pass=body.require_argus_pass,
        only_task_ids=body.task_ids,
    )
    integration_branch = f"daedalus-merge-{uuid.uuid4()}"
    plans = await plan_batch(
        workspace_path=project.workspace_path,
        default_branch=project.git_default_branch,
        candidates=candidates,
        integration_branch=integration_branch,
    )
    verify_commands = await _verify_commands_for(db, project)
    settings = get_settings()
    batch_result = await execute_batch(
        db=db,
        project_id=project.id,
        workspace_path=project.workspace_path,
        default_branch=project.git_default_branch,
        plans=plans,
        verify_commands=verify_commands,
        require_argus_pass=body.require_argus_pass,
        created_by_user_id=user.id,
        agent_uid=settings.agent_uid,
        agent_gid=settings.agent_gid,
    )

    await audit_record(
        db,
        actor_user_id=user.id,
        action="project.merge_batch",
        target_kind="project",
        target_id=str(project.id),
        payload={
            "batch_id": str(batch_result.batch_id),
            "integration_branch": batch_result.integration_branch,
            "merged": batch_result.merged_count,
            "skipped": batch_result.skipped_count,
            "verify_exit_code": batch_result.verify_exit_code,
            "error": batch_result.error,
        },
    )
    await db.commit()

    batch, items = await _load_batch(db, project.id, batch_result.batch_id, user)
    return _batch_to_out(batch, items)


@router.get("/{pid}/merge-batches", response_model=list[MergeBatchOut])
async def merge_batch_list(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> list[MergeBatchOut]:
    await _load_project(db, pid, user)
    res = await db.execute(
        select(MergeBatch)
        .where(MergeBatch.project_id == pid)
        .order_by(MergeBatch.created_at.desc())
    )
    out: list[MergeBatchOut] = []
    for batch in res.scalars():
        items_res = await db.execute(
            select(MergeBatchItem)
            .where(MergeBatchItem.batch_id == batch.id)
            .order_by(MergeBatchItem.created_at.asc())
        )
        out.append(_batch_to_out(batch, list(items_res.scalars())))
    return out


@router.get("/{pid}/merge-batches/{bid}", response_model=MergeBatchOut)
async def merge_batch_get(
    pid: uuid.UUID,
    bid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> MergeBatchOut:
    # Update item states from their resolution runs before returning.
    # reconcile_resolution_states also flips the batch from `resolving` to
    # `awaiting_review` when everything is terminal — that mutation can
    # happen even when no item changed in *this* call (e.g. the previous
    # /resolve already auto-merged the last conflict), so commit
    # unconditionally rather than gating on the items-changed list.
    changed = await reconcile_resolution_states(db, bid)
    await db.commit()
    batch, items = await _load_batch(db, pid, bid, user)
    if changed:
        await db.refresh(batch)
    return _batch_to_out(batch, items)


@router.post(
    "/{pid}/merge-batches/{bid}/resolve",
    response_model=ResolutionStepOut | None,
)
async def merge_batch_resolve_next(
    pid: uuid.UUID,
    bid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> ResolutionStepOut | None:
    batch, _ = await _load_batch(db, pid, bid, user)
    step = await resolve_next_conflict(db, batch.id)
    step_payload = (
        {
            "item_id": str(step.item_id),
            "branch": step.branch,
            "state": step.state,
            "task_id": str(step.task_id) if step.task_id else None,
            "run_id": str(step.run_id) if step.run_id else None,
            "error": step.error,
        }
        if step
        else None
    )
    await audit_record(
        db,
        actor_user_id=user.id,
        action="project.merge_batch.resolve_next",
        target_kind="merge_batch",
        target_id=str(bid),
        payload={"step": step_payload},
    )
    await db.commit()
    if step is None:
        return None
    return ResolutionStepOut(
        item_id=step.item_id,
        branch=step.branch,
        state=step.state,
        task_id=step.task_id,
        run_id=step.run_id,
        error=step.error,
    )


@router.post("/{pid}/merge-batches/{bid}/ship", response_model=ShipResultOut)
async def merge_batch_ship(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: ShipIn,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> ShipResultOut:
    batch, _ = await _load_batch(db, pid, bid, user)
    result = await ship_batch(
        db,
        batch_id=batch.id,
        delete_source_branches=body.delete_source_branches,
        remove_worktree=body.remove_worktree,
    )
    await audit_record(
        db,
        actor_user_id=user.id,
        action="project.merge_batch.ship",
        target_kind="merge_batch",
        target_id=str(bid),
        payload={
            "state": result.state,
            "default_branch": result.default_branch,
            "pruned_branches": result.pruned_branches,
            "removed_worktree": result.removed_worktree,
            "error": result.error,
        },
    )
    await db.commit()
    return ShipResultOut(
        state=result.state,
        integration_branch=result.integration_branch,
        default_branch=result.default_branch,
        pruned_branches=result.pruned_branches,
        removed_worktree=result.removed_worktree,
        error=result.error,
    )


class UndoResultOut(BaseModel):
    state: str
    default_branch: str
    reset_to: str | None = None
    error: str | None = None


@router.post("/{pid}/merge-batches/{bid}/undo", response_model=UndoResultOut)
async def merge_batch_undo(
    pid: uuid.UUID,
    bid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
) -> UndoResultOut:
    """One-click undo of a ship: reset the default branch to its pre-ship tip
    (refused if it has advanced past the shipped commit)."""
    batch, _ = await _load_batch(db, pid, bid, user)
    result = await undo_ship(db, batch_id=batch.id)
    await audit_record(
        db,
        actor_user_id=user.id,
        action="project.merge_batch.undo",
        target_kind="merge_batch",
        target_id=str(bid),
        payload={
            "state": result.state,
            "default_branch": result.default_branch,
            "reset_to": result.reset_to,
            "error": result.error,
        },
    )
    await db.commit()
    return UndoResultOut(
        state=result.state,
        default_branch=result.default_branch,
        reset_to=result.reset_to,
        error=result.error,
    )
