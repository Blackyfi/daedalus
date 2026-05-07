"""Cost-threshold trip wire fed off `Run.cost_usd_micros`.

Project owners can opt in to a `usage_threshold_micros` ceiling. After
each run completes, the scheduler asks `maybe_notify_usage_threshold` to
re-sum the project's lifetime cost; if any owner's threshold was just
crossed (i.e. the previous total was below it and the new total is at or
above it), one notification fires for that owner.

The pre-vs-post comparison is what guards against re-firing on every
subsequent run after the threshold is exceeded — without it a single
breach would page the user on every run thereafter.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import Project, Run, User, UserNotificationPref

from daedalus.notifications.dispatcher import notify
from daedalus.notifications.events import NotificationEvent, NotificationKind

log = structlog.get_logger()


async def maybe_notify_usage_threshold(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    delta_cost_micros: int,
) -> int:
    """Compare pre/post project cost against each owner's threshold and
    notify on a fresh crossing. Returns the number of crossings reported."""
    if delta_cost_micros <= 0:
        return 0

    new_total = await _project_cost_micros(session, project_id)
    if new_total is None:
        return 0
    prev_total = new_total - delta_cost_micros
    if prev_total < 0:
        prev_total = 0

    project = await session.get(Project, project_id)
    if project is None:
        return 0

    owner_pref = await _owner_pref(session, project.owner_id)
    if owner_pref is None or owner_pref.usage_threshold_micros is None:
        return 0
    threshold = int(owner_pref.usage_threshold_micros)
    if not (prev_total < threshold <= new_total):
        return 0

    event = NotificationEvent(
        kind=NotificationKind.usage_threshold,
        title=f"Usage threshold reached for project {project.name}",
        body=(
            f"Cumulative LLM cost on project '{project.name}' has reached "
            f"${new_total / 1_000_000:.2f}, crossing your configured "
            f"threshold of ${threshold / 1_000_000:.2f}."
        ),
        project_id=project.id,
        run_id=run_id,
        metadata={
            "cost_usd_micros": new_total,
            "threshold_usd_micros": threshold,
            "previous_cost_usd_micros": prev_total,
        },
    )
    delivered = await notify(event, session)
    log.info(
        "notifications.usage_threshold_crossed",
        project_id=str(project_id),
        threshold_usd_micros=threshold,
        new_total_usd_micros=new_total,
        delivered=delivered,
    )
    return 1


async def _project_cost_micros(
    session: AsyncSession, project_id: uuid.UUID
) -> int | None:
    res = await session.execute(
        select(func.coalesce(func.sum(Run.cost_usd_micros), 0)).where(
            Run.project_id == project_id
        )
    )
    total = res.scalar_one()
    if total is None:
        return None
    return int(total)


async def _owner_pref(
    session: AsyncSession, owner_id: uuid.UUID
) -> UserNotificationPref | None:
    res = await session.execute(
        select(UserNotificationPref).where(UserNotificationPref.user_id == owner_id)
    )
    return res.scalar_one_or_none()
