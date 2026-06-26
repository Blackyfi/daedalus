"""Tiny async SMTP sender. Best-effort — log on failure rather than crash login."""
from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage

import aiosmtplib

from daedalus.core.logging import log
from daedalus.core.settings import get_settings


async def send_otp_email(*, to: str, code: str, magic_link: str, expires_at: datetime) -> None:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = "Daedalus sign-in code"
    msg.set_content(
        f"Daedalus sign-in code: {code}\n\n"
        f"Or click: {magic_link}\n\n"
        f"Expires at: {expires_at.isoformat()}\n\n"
        "If you did not initiate this sign-in, ignore this email and rotate your password.\n"
    )
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=settings.smtp_tls,
        )
    except Exception as exc:
        log.error("smtp.send_failed", error=str(exc), to=to)
