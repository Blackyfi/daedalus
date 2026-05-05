"""Project notes CRUD."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import NoteIn, NoteOut, NotePatch
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import Note, Project, Role, User

router = APIRouter()


async def _project(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    result = await db.execute(select(Project).where(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and project.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return project


@router.get("/projects/{pid}/notes", response_model=list[NoteOut])
async def list_notes(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    result = await db.execute(select(Note).where(Note.project_id == pid).order_by(Note.updated_at.desc()))
    return result.scalars().all()


@router.post("/projects/{pid}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    pid: uuid.UUID,
    body: NoteIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    await _project(db, pid, user)
    note = Note(project_id=pid, title=body.title, body=body.body)
    db.add(note)
    await db.flush()
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="note.create",
        target_kind="note",
        target_id=str(note.id),
    )
    await db.commit()
    return note


@router.patch("/notes/{nid}", response_model=NoteOut)
async def patch_note(
    nid: uuid.UUID,
    body: NotePatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(Note).where(Note.id == nid))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    await _project(db, note.project_id, user)
    fields = body.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(note, key, value)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="note.update",
        target_kind="note",
        target_id=str(note.id),
        payload=fields,
    )
    await db.commit()
    return note


@router.delete("/notes/{nid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    nid: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(Note).where(Note.id == nid))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    await _project(db, note.project_id, user)
    await db.delete(note)
    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="note.delete",
        target_kind="note",
        target_id=str(nid),
    )
    await db.commit()
