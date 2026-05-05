"""Helpers for writing to the audit log."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import AuditEvent


async def record(
    db: AsyncSession,
    *,
    actor_user_id=None,
    actor_ip: str | None = None,
    actor_cert_fp: str | None = None,
    action: str,
    target_kind: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            actor_cert_fp=actor_cert_fp,
            action=action,
            target_kind=target_kind,
            target_id=str(target_id) if target_id is not None else None,
            payload=payload or {},
        )
    )
    await db.flush()
