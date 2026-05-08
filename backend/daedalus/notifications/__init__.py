"""User-facing notifications: task lifecycle + usage thresholds.

Other subsystems (Hermes scheduler, Talos usage monitor) call
``notify(event, session)`` to fan a `NotificationEvent` out to every user
whose `UserNotificationPref` row opts in to the event's kind on a given
channel. Channels are pluggable (email + in-app SSE today).
"""
from __future__ import annotations

from daedalus.notifications.dispatcher import notify
from daedalus.notifications.events import NotificationEvent, NotificationKind

__all__ = ["NotificationEvent", "NotificationKind", "notify"]
