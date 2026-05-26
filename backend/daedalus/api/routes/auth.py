"""3-step login: password → email OTP → TOTP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth import audit, email_otp, totp
from daedalus.auth.dependencies import NO_MTLS_SENTINEL
from daedalus.auth.passwords import needs_rehash, hash_password, verify_password
from daedalus.auth.sessions import (
    COOKIE_NAME,
    create_session,
    load_session,
    revoke_session,
    sign_session_id,
    unsign_session_id,
)
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import User

router = APIRouter()


class PasswordIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class OTPIn(BaseModel):
    email: EmailStr
    code: str | None = None
    token: str | None = None


class TOTPIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=10)


def _ip(req: Request) -> str | None:
    fwd = req.headers.get("x-real-ip") or req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else None


def _resolve_cert_fp(cert_fp: str | None) -> str:
    """Mirror `auth.dependencies.current_cert_fingerprint`."""
    if cert_fp:
        return cert_fp.lower()
    if not get_settings().require_client_cert:
        return NO_MTLS_SENTINEL
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing client certificate")


async def _load_user(db: AsyncSession, email: str) -> User | None:
    res = await db.execute(select(User).where(User.email == email.lower()))
    return res.scalar_one_or_none()


def _locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(timezone.utc)


@router.post("/password", status_code=status.HTTP_202_ACCEPTED)
async def step_password(
    body: PasswordIn,
    request: Request,
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    db: AsyncSession = Depends(get_session),
):
    """Step 1: verify password. On success issue an email OTP."""
    settings = get_settings()
    user = await _load_user(db, body.email)

    # Constant-ish failure path so timing doesn't disclose enrolment.
    if user is None or _locked(user) or not verify_password(body.password, user.password_hash):
        if user is not None and not _locked(user):
            user.failed_login_count += 1
            if user.failed_login_count >= settings.lockout_threshold:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.lockout_minutes)
        await audit.record(
            db, actor_user_id=getattr(user, "id", None), actor_ip=_ip(request),
            actor_cert_fp=cert_fp, action="auth.password_fail", target_kind="user",
            target_id=body.email, payload={},
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    user.failed_login_count = 0
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    if cert_fp and user.pinned_cert_fingerprint and user.pinned_cert_fingerprint != cert_fp:
        await audit.record(
            db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
            action="auth.cert_mismatch", target_kind="user", target_id=str(user.id),
        )
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate does not match enrolled cert")

    await email_otp.issue(db, user, ip=_ip(request), cert_fp=cert_fp)
    await audit.record(
        db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
        action="auth.password_ok", target_kind="user", target_id=str(user.id),
    )
    await db.commit()
    return {"status": "otp_sent"}


@router.post("/email-otp", status_code=status.HTTP_202_ACCEPTED)
async def step_email_otp(
    body: OTPIn,
    request: Request,
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    db: AsyncSession = Depends(get_session),
):
    user = await _load_user(db, body.email)
    if user is None or _locked(user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid")
    ok = await email_otp.verify(db, user, code=body.code, token=body.token)
    if not ok:
        await audit.record(
            db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
            action="auth.otp_fail", target_kind="user", target_id=str(user.id),
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "otp invalid or expired")
    await audit.record(
        db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
        action="auth.otp_ok", target_kind="user", target_id=str(user.id),
    )
    await db.commit()
    return {"status": "totp_required"}


@router.post("/totp")
async def step_totp(
    body: TOTPIn,
    request: Request,
    response: Response,
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    db: AsyncSession = Depends(get_session),
):
    cert_fp = _resolve_cert_fp(cert_fp)
    user = await _load_user(db, body.email)
    if user is None or _locked(user) or not user.totp_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid")

    if not totp.verify_totp(user.totp_secret, body.code):
        # try as recovery code
        ok, remaining = totp.consume_recovery_code(user.recovery_codes_hash, body.code)
        if not ok:
            await audit.record(
                db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
                action="auth.totp_fail", target_kind="user", target_id=str(user.id),
            )
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "totp invalid")
        user.recovery_codes_hash = remaining

    if not user.pinned_cert_fingerprint:
        user.pinned_cert_fingerprint = cert_fp
    user.last_login_at = datetime.now(timezone.utc)

    sess = await create_session(db, user, cert_fp=cert_fp, ip=_ip(request))
    cookie = sign_session_id(sess.id)
    response.set_cookie(
        COOKIE_NAME, cookie,
        max_age=get_settings().session_hard_hours * 3600,
        httponly=True, secure=True, samesite="strict", path="/",
    )
    await audit.record(
        db, actor_user_id=user.id, actor_ip=_ip(request), actor_cert_fp=cert_fp,
        action="auth.login", target_kind="user", target_id=str(user.id),
    )
    await db.commit()
    return {"status": "ok", "user": {"id": str(user.id), "email": user.email, "role": user.role.value}}


@router.get("/status")
async def status_probe(
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    session_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: AsyncSession = Depends(get_session),
):
    """Boot probe used by the SPA to decide login-vs-shell.

    Always 200 — never raises 401. Returning 200 with ``authenticated=false``
    keeps the browser console clean (a 401 from the auth probe at every
    page load looks like a bug, even though it's the intended signal).
    """
    fp = (cert_fp or "").lower() or (
        NO_MTLS_SENTINEL if not get_settings().require_client_cert else None
    )
    if not session_cookie or not fp:
        return {"authenticated": False, "user": None}
    sid = unsign_session_id(
        session_cookie,
        max_age_seconds=get_settings().session_hard_hours * 3600,
    )
    if sid is None:
        return {"authenticated": False, "user": None}
    loaded = await load_session(db, sid, cert_fp=fp)
    if not loaded:
        return {"authenticated": False, "user": None}
    _, user = loaded
    return {
        "authenticated": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "role": user.role.value,
        },
    }


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    session_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    db: AsyncSession = Depends(get_session),
):
    if session_cookie:
        sid = unsign_session_id(session_cookie, max_age_seconds=24 * 3600 * 7)
        if sid:
            await revoke_session(db, sid)
            await audit.record(
                db, actor_ip=_ip(request), actor_cert_fp=cert_fp,
                action="auth.logout", target_kind="session", target_id=str(sid),
            )
            await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}
