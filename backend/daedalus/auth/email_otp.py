"""Email OTP — 8-digit code + magic link, both single-use, 15-min TTL (§10.2)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth.smtp import send_otp_email
from daedalus.core.settings import get_settings
from daedalus.db.models import EmailOTP, User

OTP_TTL_MINUTES = 15


def _hmac(value: str) -> str:
    pepper = get_settings().password_pepper.encode()
    return hashlib.blake2b(value.encode(), key=pepper, digest_size=32).hexdigest()


def _gen_code() -> str:
    return f"{secrets.randbelow(10**8):08d}"


def _gen_token() -> str:
    return secrets.token_urlsafe(32)


async def issue(
    session: AsyncSession,
    user: User,
    *,
    ip: str | None,
    cert_fp: str | None,
) -> EmailOTP:
    code = _gen_code()
    token = _gen_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    otp = EmailOTP(
        user_id=user.id,
        code_hash=_hmac(code),
        magic_token_hash=_hmac(token),
        expires_at=expires_at,
        issued_ip=ip,
        issued_cert_fp=cert_fp,
    )
    session.add(otp)
    await session.flush()

    public_url = get_settings().public_url.rstrip("/")
    magic_link = f"{public_url}/login/email-link?token={token}&otp_id={otp.id}"
    await send_otp_email(to=user.email, code=code, magic_link=magic_link, expires_at=expires_at)
    return otp


async def verify(
    session: AsyncSession,
    user: User,
    *,
    code: str | None = None,
    token: str | None = None,
) -> bool:
    """Returns True if either the code or the magic-link token is valid + unused.

    Successful use marks the OTP consumed.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(EmailOTP)
        .where(EmailOTP.user_id == user.id)
        .where(EmailOTP.used_at.is_(None))
        .where(EmailOTP.expires_at > now)
        .order_by(EmailOTP.created_at.desc())
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()

    code_hash = _hmac(code) if code else None
    token_hash = _hmac(token) if token else None
    for otp in candidates:
        if (code_hash and otp.code_hash == code_hash) or (token_hash and otp.magic_token_hash == token_hash):
            otp.used_at = now
            await session.flush()
            return True
    return False
