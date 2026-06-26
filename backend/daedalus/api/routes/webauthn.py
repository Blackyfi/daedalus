"""WebAuthn registration + authentication routes.

Challenge state is held in Redis under `webauthn:challenge:reg:<user_id>` and
`webauthn:challenge:auth:<email>` with a 5-minute TTL — survives the round
trip between begin and finish without sticky sessions.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth import audit, webauthn_svc
from daedalus.auth.dependencies import NO_MTLS_SENTINEL, current_user
from daedalus.auth.sessions import COOKIE_NAME, create_session, sign_session_id
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import User
from daedalus.db.redis import get_redis

router = APIRouter()


_REG_TTL = 300
_AUTH_TTL = 300


def _ip(req: Request) -> str | None:
    fwd = req.headers.get("x-real-ip") or req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else None


def _resolve_cert_fp(cert_fp: str | None) -> str:
    """Mirror the policy in `auth.dependencies.current_cert_fingerprint`."""
    if cert_fp:
        return cert_fp.lower()
    if not get_settings().require_client_cert:
        return NO_MTLS_SENTINEL
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing client certificate")


# --- registration (authenticated user enrolls a new key) -------------------

class RegisterFinishIn(BaseModel):
    response: dict[str, Any]
    nickname: str | None = None


@router.post("/register/begin")
async def register_begin(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    options, challenge_b64 = await webauthn_svc.begin_registration(db, user)
    redis = get_redis()
    await redis.set(f"webauthn:challenge:reg:{user.id}", challenge_b64, ex=_REG_TTL)
    return options


@router.post("/register/finish")
async def register_finish(
    body: RegisterFinishIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    redis = get_redis()
    challenge_b64 = await redis.get(f"webauthn:challenge:reg:{user.id}")
    if not challenge_b64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "registration challenge expired or missing")
    if isinstance(challenge_b64, bytes):
        challenge_b64 = challenge_b64.decode()

    try:
        cred = await webauthn_svc.finish_registration(
            db, user, challenge_b64, body.response, nickname=body.nickname
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"registration failed: {exc}") from exc

    await audit.record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="auth.webauthn_register",
        target_kind="user",
        target_id=str(user.id),
        payload={"credential_pk": str(cred.id), "nickname": cred.nickname},
    )
    await db.commit()
    await redis.delete(f"webauthn:challenge:reg:{user.id}")
    return {"status": "ok", "credential": {"id": str(cred.id), "nickname": cred.nickname}}


@router.get("/credentials")
async def list_my_credentials(
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    creds = await webauthn_svc.list_credentials(db, user)
    return [
        {
            "id": str(c.id),
            "nickname": c.nickname,
            "transports": c.transports,
            "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in creds
    ]


@router.delete("/credentials/{credential_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_credential(
    credential_pk: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    ok = await webauthn_svc.delete_credential(db, user, credential_pk)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=request.state.cert_fp,
        action="auth.webauthn_delete",
        target_kind="user",
        target_id=str(user.id),
        payload={"credential_pk": credential_pk},
    )
    await db.commit()


# --- authentication (replaces TOTP step at login) -------------------------

class AuthBeginIn(BaseModel):
    email: EmailStr


class AuthFinishIn(BaseModel):
    email: EmailStr
    response: dict[str, Any]


async def _user_or_401(db: AsyncSession, email: str) -> User:
    res = await db.execute(select(User).where(User.email == email.lower()))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid")
    return user


@router.post("/authenticate/begin")
async def authenticate_begin(
    body: AuthBeginIn,
    db: AsyncSession = Depends(get_session),
):
    user = await _user_or_401(db, body.email)
    options, challenge_b64 = await webauthn_svc.begin_authentication(db, user)
    redis = get_redis()
    await redis.set(f"webauthn:challenge:auth:{user.email}", challenge_b64, ex=_AUTH_TTL)
    return options


@router.post("/authenticate/finish")
async def authenticate_finish(
    body: AuthFinishIn,
    request: Request,
    response: Response,
    cert_fp: Annotated[str | None, Header(alias="X-Client-Cert-Fingerprint")] = None,
    db: AsyncSession = Depends(get_session),
):
    cert_fp = _resolve_cert_fp(cert_fp)
    user = await _user_or_401(db, body.email)

    redis = get_redis()
    challenge_b64 = await redis.get(f"webauthn:challenge:auth:{user.email}")
    if not challenge_b64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "authentication challenge expired or missing")
    if isinstance(challenge_b64, bytes):
        challenge_b64 = challenge_b64.decode()

    try:
        await webauthn_svc.finish_authentication(db, user, challenge_b64, body.response)
    except Exception as exc:
        await audit.record(
            db,
            actor_user_id=user.id,
            actor_ip=_ip(request),
            actor_cert_fp=cert_fp,
            action="auth.webauthn_fail",
            target_kind="user",
            target_id=str(user.id),
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"webauthn failed: {exc}") from exc

    if not user.pinned_cert_fingerprint:
        user.pinned_cert_fingerprint = cert_fp
    user.last_login_at = datetime.now(UTC)

    sess = await create_session(db, user, cert_fp=cert_fp, ip=_ip(request))
    cookie = sign_session_id(sess.id)
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=get_settings().session_hard_hours * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    await audit.record(
        db,
        actor_user_id=user.id,
        actor_ip=_ip(request),
        actor_cert_fp=cert_fp,
        action="auth.login",
        target_kind="user",
        target_id=str(user.id),
        payload={"factor": "webauthn"},
    )
    await db.commit()
    await redis.delete(f"webauthn:challenge:auth:{user.email}")
    return {"status": "ok", "user": {"id": str(user.id), "email": user.email, "role": user.role.value}}
