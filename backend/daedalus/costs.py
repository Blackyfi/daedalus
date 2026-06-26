"""Per-project spend helpers for the monthly cost cap.

Run cost is parsed at completion and persisted on ``runs.cost_usd_micros``
(USD micros, 1_000_000 = $1.00). The cap on ``Project`` blocks new task runs
once the current calendar month's run cost reaches it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import Run


def month_start(now: datetime | None = None) -> datetime:
    """First instant of the current UTC calendar month."""
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_cost_usd_micros(db: AsyncSession, project_id: uuid.UUID) -> int:
    """Sum of run cost for the project since the start of this UTC month."""
    res = await db.execute(
        select(func.coalesce(func.sum(Run.cost_usd_micros), 0)).where(
            Run.project_id == project_id,
            Run.created_at >= month_start(),
        )
    )
    return int(res.scalar_one() or 0)


def over_cap(cap_usd_micros: int | None, spent_usd_micros: int) -> bool:
    """True when a cap is set and this month's spend has reached it."""
    return cap_usd_micros is not None and spent_usd_micros >= cap_usd_micros
