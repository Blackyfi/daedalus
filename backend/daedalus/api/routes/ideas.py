"""Idea Box CRUD + 'plan-from-ideas' planning trigger."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import IdeaIn, IdeaOut, IdeaPatch
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import Idea, Project, Role, User
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


@router.get("/projects/{pid}/ideas", response_model=list[IdeaOut])
async def list_ideas(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    res = await db.execute(
        select(Idea).where(Idea.project_id == pid).order_by(Idea.sort_index.asc(), Idea.created_at.asc())
    )
    return res.scalars().all()


@router.post("/projects/{pid}/ideas", response_model=IdeaOut, status_code=status.HTTP_201_CREATED)
async def create_idea(
    pid: uuid.UUID,
    body: IdeaIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    idea = Idea(project_id=pid, text=body.text, tags=body.tags)
    db.add(idea)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="idea.create", target_kind="idea", target_id=str(idea.id),
    )
    await db.commit()
    return idea


@router.patch("/ideas/{iid}", response_model=IdeaOut)
async def patch_idea(
    iid: uuid.UUID,
    body: IdeaPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    res = await db.execute(select(Idea).where(Idea.id == iid))
    idea = res.scalar_one_or_none()
    if not idea:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await _project(db, idea.project_id, user)
    # Ideas become non-editable once they've been promoted to a task
    # (the plan-confirm flow flips `archived` on every source idea).
    if idea.archived:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "idea has been promoted to a task and is no longer editable",
        )
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return idea
    for k, v in updates.items():
        setattr(idea, k, v)
    idea.updated_at = datetime.now(UTC)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="idea.update",
        target_kind="idea",
        target_id=str(idea.id),
        payload={"fields": sorted(updates.keys())},
    )
    await db.commit()
    return idea


@router.delete("/ideas/{iid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_idea(
    iid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    res = await db.execute(select(Idea).where(Idea.id == iid))
    idea = res.scalar_one_or_none()
    if not idea:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await _project(db, idea.project_id, user)
    await db.delete(idea)
    await db.commit()


@router.post("/projects/{pid}/plan", status_code=status.HTTP_202_ACCEPTED)
async def trigger_plan(
    pid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    proj = await _project(db, pid, user)
    run = await HermesClient(db).enqueue_planning(proj)
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="plan.enqueue", target_kind="project", target_id=str(pid),
        payload={"run_id": str(run.id)},
    )
    await db.commit()
    return {"run_id": str(run.id), "status": "queued"}
