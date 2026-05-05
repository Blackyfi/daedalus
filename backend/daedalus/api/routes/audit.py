"""Audit log read endpoint (owner-only). §10.7."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import AuditOut
from daedalus.auth.dependencies import require_role
from daedalus.db.base import get_session
from daedalus.db.models import AuditEvent, Role

router = APIRouter()


@router.get("", response_model=list[AuditOut], dependencies=[Depends(require_role(Role.owner))])
async def list_audit(
    since: datetime | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(200, le=1000, ge=1),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(AuditEvent).order_by(AuditEvent.at.desc()).limit(limit)
    if since:
        stmt = stmt.where(AuditEvent.at >= since)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    res = await db.execute(stmt)
    return res.scalars().all()
