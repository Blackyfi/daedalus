"""Delivery channels for `NotificationEvent`s.

Each channel is a small async callable: ``await channel.send(event, user)``.
Failures are logged and swallowed — a busted SMTP server must never abort a
run completion or block another channel from firing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import TYPE_CHECKING, Protocol

import aiosmtplib
import structlog

from daedalus.core.settings import get_settings
from daedalus.db.redis import get_redis

from daedalus.notifications.events import NotificationEvent

if TYPE_CHECKING:
    from daedalus.db.models import User

log = structlog.get_logger()


class NotificationChannel(Protocol):
    name: str

    async def send(self, event: NotificationEvent, user: "User") -> None: ...


class EmailChannel:
    """Best-effort SMTP. Reuses the settings already used for OTP mail."""

    name = "email"

    async def send(self, event: NotificationEvent, user: "User") -> None:
        settings = get_settings()
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = user.email
        msg["Subject"] = f"[Daedalus] {event.title}"
        msg.set_content(_render_email_body(event, user))
        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=settings.smtp_tls,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "notifications.email_send_failed",
                error=str(exc),
                to=user.email,
                kind=event.kind.value,
            )


class InAppChannel:
    """Publishes onto the per-user notification channel for the SSE feed.

    Subscribers (Iris/web UI) listen on `events:user:<user_id>:notifications`
    and render an in-app toast / dropdown entry.
    """

    name = "in_app"

    CHANNEL_PREFIX = "events:user"

    async def send(self, event: NotificationEvent, user: "User") -> None:
        redis = get_redis()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": event.kind.value,
            "title": event.title,
            "body": event.body,
            "project_id": str(event.project_id),
            "task_id": str(event.task_id) if event.task_id else None,
            "run_id": str(event.run_id) if event.run_id else None,
            "metadata": event.metadata,
        }
        try:
            await redis.publish(
                f"{self.CHANNEL_PREFIX}:{user.id}:notifications",
                json.dumps(payload),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "notifications.in_app_publish_failed",
                error=str(exc),
                user_id=str(user.id),
                kind=event.kind.value,
            )


def _render_email_body(event: NotificationEvent, user: "User") -> str:
    lines = [
        f"Hi {user.display_name},",
        "",
        event.body,
        "",
        f"Project: {event.project_id}",
    ]
    if event.task_id is not None:
        lines.append(f"Task: {event.task_id}")
    if event.run_id is not None:
        lines.append(f"Run: {event.run_id}")
    if event.metadata:
        lines.append("")
        lines.append("Details:")
        for k, v in sorted(event.metadata.items()):
            lines.append(f"  {k}: {v}")
    lines.extend(["", "— Daedalus"])
    return "\n".join(lines)
