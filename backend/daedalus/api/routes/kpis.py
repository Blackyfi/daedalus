"""KPI time-series endpoints.

The first endpoint exposes a per-project, per-day breakdown of task
counts by status, derived from ``task_status_events``. The frontend's
KPI page renders this as a stacked area chart.

Daily buckets are computed in Postgres with ``generate_series`` + a
lateral ``DISTINCT ON`` join — one query per request regardless of
range. UTC throughout; the SPA's date label is the user's local
rendering of midnight-UTC.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import Project, Role, TaskStatus, User

router = APIRouter()


# Order matches typical lifecycle so stacked-area colours read left-to-right
# in the chart legend.
_STATUS_ORDER: list[str] = [
    TaskStatus.backlog.value,
    TaskStatus.ready.value,
    TaskStatus.in_progress.value,
    TaskStatus.verifying.value,
    TaskStatus.needs_fixes.value,
    TaskStatus.done.value,
    TaskStatus.cancelled.value,
]


async def _project_for_user(db: AsyncSession, pid: uuid.UUID, user: User) -> Project:
    res = await db.execute(select(Project).where(Project.id == pid))
    proj = res.scalar_one_or_none()
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != Role.owner and proj.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your project")
    return proj


@router.get("/projects/{pid}/task-status-timeseries")
async def task_status_timeseries(
    pid: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, object]:
    """Daily task counts by status for the given project.

    Returns:
        {
          "statuses": [...],
          "points": [
             {"date": "YYYY-MM-DD", "backlog": int, "ready": int, ...},
             ...
          ],
        }
    """
    await _project_for_user(db, pid, user)

    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)

    # For each day in [start, today], find the latest status event per task
    # whose `at` is <= end-of-day, then count per status. The lateral join
    # picks at most one event per task per day, so the row count stays
    # bounded by (days x tasks-with-events) rather than blowing up.
    query = text(
        """
        WITH days AS (
            SELECT generate_series(CAST(:start_date AS date), CAST(:end_date AS date), '1 day')::date AS d
        ),
        latest AS (
            SELECT d.d AS day, lateral_event.to_status
            FROM days d
            CROSS JOIN LATERAL (
                SELECT DISTINCT ON (e.task_id) e.task_id, e.to_status
                FROM task_status_events e
                WHERE e.project_id = :pid
                  AND e.at < ((d.d + 1)::timestamp AT TIME ZONE 'UTC')
                ORDER BY e.task_id, e.at DESC
            ) lateral_event
        )
        SELECT day, to_status, COUNT(*)::int AS count
        FROM latest
        GROUP BY day, to_status
        """
    )
    rows = (
        await db.execute(
            query,
            {"start_date": start, "end_date": today, "pid": pid},
        )
    ).all()

    by_day: dict[date, dict[str, int]] = {}
    cur = start
    while cur <= today:
        by_day[cur] = {s: 0 for s in _STATUS_ORDER}
        cur = cur + timedelta(days=1)

    for row in rows:
        day_val: date = row[0]
        status_val = row[1]
        # Postgres returns the enum as its string value; SQLAlchemy may
        # also surface it as the TaskStatus python enum depending on
        # adapter config. Normalise to its string form.
        key = status_val.value if hasattr(status_val, "value") else str(status_val)
        if day_val in by_day:
            by_day[day_val][key] = row[2]

    points = [
        {"date": d.isoformat(), **by_day[d]}
        for d in sorted(by_day.keys())
    ]
    return {"statuses": _STATUS_ORDER, "points": points}
