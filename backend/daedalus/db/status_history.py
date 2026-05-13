"""Capture every `Task.status` transition into ``task_status_events``.

A single SQLAlchemy ``before_flush`` listener does the work — it inspects
the session's `new` and `dirty` sets, detects status changes via the
attribute history, and adds matching ``TaskStatusEvent`` rows to the same
flush. This avoids touching the dozen-plus call sites that mutate
``task.status`` (routes, hermes, cli, scheduler).

The KPI time-series endpoint reads these rows to reconstruct, for each
day in a range, the count of tasks per status (latest event per task at
end-of-day).
"""
from __future__ import annotations

import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from daedalus.db.models import Task, TaskStatus, TaskStatusEvent


@event.listens_for(Session, "before_flush")
def _record_task_status_changes(session: Session, _flush_context, _instances) -> None:
    events: list[TaskStatusEvent] = []

    # Newly-inserted tasks: emit a NULL -> initial status event.
    for obj in session.new:
        if isinstance(obj, Task):
            # Both `Task.id` (`default=uuid.uuid4`) and `Task.status`
            # (`default=TaskStatus.backlog`) have column defaults applied
            # by SQLAlchemy at INSERT time — during `before_flush` the
            # attributes are still None, and emitting a TaskStatusEvent
            # with NULL task_id or to_status violates the table's NOT
            # NULL constraints. Pre-resolve them ourselves; SQLAlchemy's
            # default machinery skips the callable when the attribute is
            # already set, so the same value is used for the actual
            # INSERT.
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.status is None:
                obj.status = TaskStatus.backlog
            events.append(
                TaskStatusEvent(
                    id=uuid.uuid4(),
                    task_id=obj.id,
                    project_id=obj.project_id,
                    from_status=None,
                    to_status=obj.status,
                )
            )

    # Updated tasks: emit only when the status attribute actually changed.
    for obj in session.dirty:
        if not isinstance(obj, Task):
            continue
        hist = inspect(obj).attrs.status.history
        if not hist.has_changes():
            continue
        # `deleted` holds the pre-update value(s); empty for objects loaded
        # without their old state in this session. Fall back to None then.
        old_status = hist.deleted[0] if hist.deleted else None
        new_status = obj.status
        if old_status == new_status:
            continue
        events.append(
            TaskStatusEvent(
                id=uuid.uuid4(),
                task_id=obj.id,
                project_id=obj.project_id,
                from_status=old_status,
                to_status=new_status,
            )
        )

    for ev in events:
        session.add(ev)
