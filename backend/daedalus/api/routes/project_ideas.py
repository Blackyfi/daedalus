"""Project-idea CRUD + promote-to-project for the Projects landing page.

These are *not* per-project ideas — they live above the project layer
and represent things the user is considering turning into a real project.
The promote endpoint creates a `Project` row, optionally `git init`s the
workspace, and flips the idea's status to `promoted`.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.routes.projects import _canonicalize_workspace
from daedalus.api.schemas import (
    ProjectIdeaIn,
    ProjectIdeaOut,
    ProjectIdeaPatch,
    ProjectIdeaPromote,
    ProjectOut,
)
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import (
    Project,
    ProjectIdea,
    ProjectIdeaStatus,
    Role,
    User,
)

router = APIRouter()


def _visible_filter(stmt, user: User):
    """Owners (platform admins) see every project idea; members see their own."""
    if user.role != Role.owner:
        return stmt.where(ProjectIdea.owner_id == user.id)
    return stmt


async def _idea_or_404(db: AsyncSession, iid: uuid.UUID, user: User) -> ProjectIdea:
    res = await db.execute(select(ProjectIdea).where(ProjectIdea.id == iid))
    idea = res.scalar_one_or_none()
    if not idea:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project idea not found")
    if user.role != Role.owner and idea.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project idea")
    return idea


@router.get("/project-ideas", response_model=list[ProjectIdeaOut])
async def list_project_ideas(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    stmt = _visible_filter(select(ProjectIdea), user).order_by(
        ProjectIdea.sort_index.asc(), ProjectIdea.created_at.asc()
    )
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post(
    "/project-ideas",
    response_model=ProjectIdeaOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_idea(
    body: ProjectIdeaIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    idea = ProjectIdea(owner_id=user.id, text=body.text, tags=body.tags)
    db.add(idea)
    await db.flush()
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project_idea.create",
        target_kind="project_idea",
        target_id=str(idea.id),
    )
    await db.commit()
    return idea


@router.patch("/project-ideas/{iid}", response_model=ProjectIdeaOut)
async def patch_project_idea(
    iid: uuid.UUID,
    body: ProjectIdeaPatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    idea = await _idea_or_404(db, iid, user)
    if idea.status == ProjectIdeaStatus.promoted:
        # Once promoted, the idea row is read-only — the Project it spawned
        # owns the editable surface from then on.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "idea has been promoted and is no longer editable",
        )
    updates = body.model_dump(exclude_unset=True)
    if updates.get("status") == ProjectIdeaStatus.promoted:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "use POST /project-ideas/{id}/promote to promote",
        )
    if not updates:
        return idea
    for k, v in updates.items():
        setattr(idea, k, v)
    idea.updated_at = datetime.now(timezone.utc)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project_idea.update",
        target_kind="project_idea",
        target_id=str(idea.id),
        payload={"fields": sorted(updates.keys())},
    )
    await db.commit()
    return idea


@router.delete(
    "/project-ideas/{iid}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_project_idea(
    iid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    idea = await _idea_or_404(db, iid, user)
    await db.delete(idea)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project_idea.delete",
        target_kind="project_idea",
        target_id=str(idea.id),
    )
    await db.commit()


@router.post(
    "/project-ideas/{iid}/promote",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def promote_project_idea(
    iid: uuid.UUID,
    body: ProjectIdeaPromote,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    idea = await _idea_or_404(db, iid, user)
    if idea.status == ProjectIdeaStatus.promoted:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "idea has already been promoted"
        )
    workspace = _canonicalize_workspace(body.workspace_path)
    if body.init_git:
        # Best-effort: create the directory and run `git init` if it isn't
        # already a repo. Failures bubble up so the user sees the error
        # rather than landing with a broken project row.
        try:
            os.makedirs(workspace, exist_ok=True)
            if not os.path.isdir(os.path.join(workspace, ".git")):
                subprocess.run(
                    ["git", "init", "--initial-branch", body.git_default_branch or "main"],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = (
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
                else str(exc)
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"git init failed: {detail.strip()}",
            )
    project = Project(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        workspace_path=workspace,
        git_default_branch=body.git_default_branch,
        default_connector_id=body.default_connector_id,
    )
    db.add(project)
    await db.flush()
    idea.status = ProjectIdeaStatus.promoted
    idea.promoted_project_id = project.id
    idea.updated_at = datetime.now(timezone.utc)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project_idea.promote",
        target_kind="project_idea",
        target_id=str(idea.id),
        payload={"project_id": str(project.id), "init_git": body.init_git},
    )
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="project.create",
        target_kind="project",
        target_id=str(project.id),
        payload={"name": project.name, "from_project_idea": str(idea.id)},
    )
    await db.commit()
    return project
