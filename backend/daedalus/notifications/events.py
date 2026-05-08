"""Notification event taxonomy + payload."""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class NotificationKind(str, enum.Enum):
    """The discrete events users can subscribe to.

    `task_*` map directly to terminal `RunState`s for the task kind. The
    fix-loop spawn is reported as `task_needs_fixes`. `usage_threshold`
    fires once per project per crossing of the project's cost ceiling
    (configured per `UserNotificationPref.usage_threshold_micros`).
    """

    task_completed = "task_completed"
    task_failed = "task_failed"
    task_needs_fixes = "task_needs_fixes"
    usage_threshold = "usage_threshold"


@dataclass(frozen=True)
class NotificationEvent:
    """Carries everything a channel needs to render & deliver a notification.

    `project_id` is required so the dispatcher can scope recipients —
    project owners and members get notified, not every user in the system.
    """

    kind: NotificationKind
    title: str
    body: str
    project_id: uuid.UUID
    task_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
