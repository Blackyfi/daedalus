"""Per-event fan-out: resolve recipients → consult prefs → emit on channels.

Recipient resolution is project-scoped: the project owner is always
considered. (When membership tables land we extend
`_resolve_project_users` to include them — until then a single owner is
the right and minimal answer.)

`notify` never raises. Each (user, channel) call is independently
guarded so a flaky channel can't poison the rest of the fan-out.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import Project, User, UserNotificationPref

from daedalus.notifications.channels import (
    EmailChannel,
    InAppChannel,
    NotificationChannel,
)
from daedalus.notifications.events import NotificationEvent, NotificationKind

log = structlog.get_logger()


_DEFAULT_CHANNELS: tuple[NotificationChannel, ...] = (EmailChannel(), InAppChannel())


async def notify(
    event: NotificationEvent,
    session: AsyncSession,
    *,
    channels: Sequence[NotificationChannel] | None = None,
) -> int:
    """Deliver `event` to every eligible (user, channel). Returns the count
    of successful per-(user, channel) deliveries."""
    chans = list(channels) if channels is not None else list(_DEFAULT_CHANNELS)
    delivered = 0
    try:
        users = await _resolve_project_users(session, event.project_id)
    except Exception:
        log.exception("notifications.recipient_lookup_failed", kind=event.kind.value)
        return 0

    if not users:
        return 0

    pref_by_user = await _load_prefs(session, [u.id for u in users])

    for user in users:
        pref = pref_by_user.get(user.id) or _default_pref()
        for channel in chans:
            if not _channel_enabled(pref, event.kind, channel.name):
                continue
            try:
                await channel.send(event, user)
                delivered += 1
            except Exception:
                log.exception(
                    "notifications.channel_send_failed",
                    channel=channel.name,
                    user_id=str(user.id),
                    kind=event.kind.value,
                )
    log.info(
        "notifications.dispatched",
        kind=event.kind.value,
        project_id=str(event.project_id),
        delivered=delivered,
        recipients=len(users),
    )
    return delivered


# ── recipient resolution ──────────────────────────────────────────────────


async def _resolve_project_users(
    session: AsyncSession, project_id: uuid.UUID
) -> list[User]:
    res = await session.execute(
        select(User)
        .join(Project, Project.owner_id == User.id)
        .where(Project.id == project_id)
    )
    return list(res.scalars().unique().all())


async def _load_prefs(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, UserNotificationPref]:
    ids = list(user_ids)
    if not ids:
        return {}
    res = await session.execute(
        select(UserNotificationPref).where(UserNotificationPref.user_id.in_(ids))
    )
    return {pref.user_id: pref for pref in res.scalars().all()}


# ── pref evaluation ───────────────────────────────────────────────────────


def _default_pref() -> UserNotificationPref:
    """The shape returned for users without an explicit prefs row.

    Mirrors the column defaults — task lifecycle on, usage-threshold on,
    both channels on. Returned as a transient (un-tracked) object so
    callers never accidentally persist it.
    """
    return UserNotificationPref(
        email_task_completed=True,
        email_task_failed=True,
        email_task_needs_fixes=True,
        email_usage_threshold=True,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=None,
    )


_PREF_FIELD_BY_KIND = {
    NotificationKind.task_completed: "task_completed",
    NotificationKind.task_failed: "task_failed",
    NotificationKind.task_needs_fixes: "task_needs_fixes",
    NotificationKind.usage_threshold: "usage_threshold",
}


def _channel_enabled(
    pref: UserNotificationPref, kind: NotificationKind, channel: str
) -> bool:
    field = _PREF_FIELD_BY_KIND.get(kind)
    if field is None:
        return False
    attr = f"{channel}_{field}"
    return bool(getattr(pref, attr, False))
