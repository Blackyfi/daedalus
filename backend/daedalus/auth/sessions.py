"""Cookie sessions, signed + cert-bound (§10.2)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from itsdangerous import BadSignature, TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.core.settings import get_settings
from daedalus.db.models import Session as SessionModel
from daedalus.db.models import User

COOKIE_NAME = "daedalus_session"


def _signer() -> TimestampSigner:
    return TimestampSigner(get_settings().session_secret, salt="daedalus.sess")


def sign_session_id(sid: uuid.UUID) -> str:
    return _signer().sign(str(sid)).decode()


def unsign_session_id(token: str, max_age_seconds: int) -> uuid.UUID | None:
    try:
        raw = _signer().unsign(token, max_age=max_age_seconds).decode()
        return uuid.UUID(raw)
    except (BadSignature, ValueError):
        return None


async def create_session(
    session: AsyncSession,
    user: User,
    *,
    cert_fp: str,
    ip: str | None,
) -> SessionModel:
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(hours=settings.session_hard_hours)
    row = SessionModel(
        user_id=user.id,
        cert_fingerprint=cert_fp,
        issued_ip=ip,
        expires_at=expires,
    )
    session.add(row)
    await session.flush()
    return row


async def load_session(
    session: AsyncSession,
    sid: uuid.UUID,
    *,
    cert_fp: str,
) -> tuple[SessionModel, User] | None:
    settings = get_settings()
    now = datetime.now(UTC)
    stmt = select(SessionModel, User).join(User, User.id == SessionModel.user_id).where(
        SessionModel.id == sid,
        SessionModel.cert_fingerprint == cert_fp,
        SessionModel.expires_at > now,
        SessionModel.revoked_at.is_(None),
    )
    res = await session.execute(stmt)
    row = res.first()
    if not row:
        return None
    sess, user = row
    if sess.last_active_at < now - timedelta(minutes=settings.session_idle_minutes):
        # idle expired — caller should re-prompt for TOTP
        return None
    sess.last_active_at = now
    await session.flush()
    return sess, user


async def revoke_session(session: AsyncSession, sid: uuid.UUID) -> None:
    res = await session.execute(select(SessionModel).where(SessionModel.id == sid))
    s = res.scalar_one_or_none()
    if s and s.revoked_at is None:
        s.revoked_at = datetime.now(UTC)
        await session.flush()
