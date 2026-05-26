"""Project auto-discovery routes — owner-only.

`GET /api/v1/discover/repos` walks the workspaces root and surfaces every
git repo found, marking which ones are already registered as Daedalus
projects.

`POST /api/v1/discover/register` takes a list of paths (with optional
per-repo overrides for name / description / default branch / connector)
and creates the corresponding Project rows in one transaction. Repos
already registered are silently skipped — the bulk-confirm UX is the
right place to re-confirm an already-known project, but doing so
should never duplicate it.
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import (
    DiscoveredRepoOut,
    DiscoverRegisterIn,
    ProjectOut,
)
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, User
from daedalus.discovery import discover

router = APIRouter()


def _require_owner(user: User) -> None:
    if user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")


def _path_inside(root: str, path: str) -> bool:
    """Strict containment check. Caller has already canonicalised both."""
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


@router.get("/repos", response_model=list[DiscoveredRepoOut])
async def list_repos(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    _require_owner(user)
    settings = get_settings()
    repos = await discover(settings.workspaces_root)

    res = await db.execute(select(Project.workspace_path))
    registered: set[str] = {row[0] for row in res.all()}

    return [
        DiscoveredRepoOut(
            name=r.name,
            path=r.path,
            relative_path=r.relative_path,
            default_branch=r.default_branch,
            description=r.description,
            last_commit_at=r.last_commit_at,
            has_uncommitted=r.has_uncommitted,
            already_registered=r.path in registered,
        )
        for r in repos
    ]


@router.post(
    "/register",
    response_model=list[ProjectOut],
    status_code=status.HTTP_201_CREATED,
)
async def register_repos(
    body: DiscoverRegisterIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    _require_owner(user)
    settings = get_settings()
    root = os.path.realpath(settings.workspaces_root)

    res = await db.execute(select(Project.workspace_path))
    registered: set[str] = {row[0] for row in res.all()}

    created: list[Project] = []
    for entry in body.repos:
        # Canonicalize before the containment check — _path_inside is a strict
        # string prefix test and assumes both sides are already realpath'd, so
        # a raw path with `..` or a symlink could otherwise escape the root.
        path = os.path.realpath(entry.path)
        if not _path_inside(root, path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"path outside workspaces root: {entry.path}",
            )
        if path in registered:
            continue

        proj = Project(
            owner_id=user.id,
            name=(entry.name or path.rsplit("/", 1)[-1])[:160],
            description=entry.description,
            workspace_path=path,
            git_default_branch=entry.git_default_branch or "main",
            default_connector_id=entry.default_connector_id,
        )
        db.add(proj)
        created.append(proj)

    if not created:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "all selected repos are already registered",
        )

    await db.flush()
    for proj in created:
        await record(
            db,
            actor_user_id=user.id,
            actor_cert_fp=request.state.cert_fp,
            action="project.create_via_discovery",
            target_kind="project",
            target_id=str(proj.id),
            payload={"name": proj.name, "path": proj.workspace_path},
        )
    await db.commit()
    return created
