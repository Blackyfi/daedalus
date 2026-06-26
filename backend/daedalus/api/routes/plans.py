"""Plan Review flow — drafts produced by the planning job land here for human review."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import PlanConfirm, PlanProposalOut, ProposedTask
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import (
    Idea,
    PlanProposal,
    PlanProposalStatus,
    Project,
    Role,
    Task,
    User,
)

router = APIRouter()


async def _project(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    res = await db.execute(select(Project).where(Project.id == pid))
    proj = res.scalar_one_or_none()
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return proj


async def _proposal_or_404(db: AsyncSession, plan_id: uuid.UUID, user: User) -> PlanProposal:
    res = await db.execute(select(PlanProposal).where(PlanProposal.id == plan_id))
    plan = res.scalar_one_or_none()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan proposal not found")
    await _project(db, plan.project_id, user)
    return plan


@router.get("/projects/{pid}/plans", response_model=list[PlanProposalOut])
async def list_proposals(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    status_filter: PlanProposalStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    stmt = select(PlanProposal).where(PlanProposal.project_id == pid)
    if status_filter is not None:
        stmt = stmt.where(PlanProposal.status == status_filter)
    stmt = stmt.order_by(PlanProposal.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()


@router.get("/plans/{plan_id}", response_model=PlanProposalOut)
async def get_proposal(
    plan_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    return await _proposal_or_404(db, plan_id, user)


@router.post("/plans/{plan_id}/confirm", response_model=PlanProposalOut)
async def confirm_proposal(
    plan_id: uuid.UUID,
    body: PlanConfirm,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    plan = await _proposal_or_404(db, plan_id, user)
    if plan.status != PlanProposalStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"plan is {plan.status.value}")

    project = await db.get(Project, plan.project_id)

    # `mode="json"` coerces UUIDs / datetimes to strings — JSONB columns
    # round-trip JSON-serialisable values only, and Pydantic v2's default
    # dump leaves `uuid.UUID` instances in place, blowing up the asyncpg
    # encoder.
    raw_tasks: list[dict[str, Any]] = (
        [t.model_dump(mode="json") for t in body.proposed_tasks]
        if body.proposed_tasks is not None
        else list(plan.proposed_tasks)
    )

    created_tasks: list[Task] = []
    for raw in raw_tasks:
        proposed = ProposedTask.model_validate(raw)
        task = Task(
            project_id=plan.project_id,
            title=proposed.title,
            description=proposed.description,
            acceptance_criteria=proposed.acceptance_criteria,
            priority=proposed.priority,
            connector_id=proposed.suggested_connector or (project.default_connector_id if project else None),
            tags=list(dict.fromkeys(proposed.tags)),
        )
        db.add(task)
        created_tasks.append(task)

    await db.flush()

    # Resolve depends_on indices into task IDs after all rows have IDs.
    for task, raw in zip(created_tasks, raw_tasks, strict=False):
        depends_indices = raw.get("depends_on") or []
        resolved: list[uuid.UUID] = []
        for idx in depends_indices:
            if isinstance(idx, int) and 0 <= idx < len(created_tasks) and idx != created_tasks.index(task):
                resolved.append(created_tasks[idx].id)
        task.depends_on = resolved

    if body.archive_source_ideas and plan.source_idea_ids:
        ideas_res = await db.execute(select(Idea).where(Idea.id.in_(plan.source_idea_ids)))
        for idea in ideas_res.scalars().all():
            idea.archived = True

    plan.status = PlanProposalStatus.confirmed
    plan.confirmed_at = datetime.now(UTC)
    if body.proposed_tasks is not None:
        plan.proposed_tasks = raw_tasks
    if body.rationale is not None:
        plan.rationale = body.rationale

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="plan.confirm",
        target_kind="plan_proposal",
        target_id=str(plan.id),
        payload={"created_task_count": len(created_tasks)},
    )
    await db.commit()
    await db.refresh(plan)
    return plan


@router.post("/plans/{plan_id}/discard", response_model=PlanProposalOut)
async def discard_proposal(
    plan_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    plan = await _proposal_or_404(db, plan_id, user)
    if plan.status != PlanProposalStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"plan is {plan.status.value}")

    plan.status = PlanProposalStatus.discarded
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="plan.discard",
        target_kind="plan_proposal",
        target_id=str(plan.id),
    )
    await db.commit()
    await db.refresh(plan)
    return plan
