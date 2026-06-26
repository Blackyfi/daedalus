"""FastAPI dependencies for authn/authz."""
from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.auth.sessions import COOKIE_NAME, load_session, unsign_session_id
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Role, User

# Sentinel used as the bound `cert_fingerprint` when mTLS is disabled at the
# proxy. We still want sessions to be bindable to *something* so the column
# stays NOT NULL and the audit log has a stable identifier.
NO_MTLS_SENTINEL = "no-mtls"

# SECURITY INVARIANT: the `X-Client-Cert-Fingerprint` header is *trusted as-is*.
# Caddy (or whatever terminates mTLS) verifies the client certificate against
# the internal CA and sets/overwrites this header before proxying. The API
# itself does NOT re-verify the cert, so a caller that can reach the API
# directly could forge an arbitrary fingerprint and bind a session to it.
# Therefore the API container MUST never be reachable except through the
# mTLS-terminating proxy — keep it off any host port / public ingress. If that
# invariant can't be guaranteed, verify the cert in-app or gate this header
# behind a shared secret known only to the proxy.


async def current_cert_fingerprint(
    x_client_cert_fingerprint: Annotated[str | None, Header()] = None,
) -> str:
    if x_client_cert_fingerprint:
        return x_client_cert_fingerprint.lower()
    if not get_settings().require_client_cert:
        return NO_MTLS_SENTINEL
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing client certificate")


async def current_user(
    request: Request,
    cert_fp: Annotated[str, Depends(current_cert_fingerprint)],
    session_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: AsyncSession = Depends(get_session),
) -> User:
    if not session_cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no session")
    settings = get_settings()
    sid = unsign_session_id(session_cookie, max_age_seconds=settings.session_hard_hours * 3600)
    if sid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")
    loaded = await load_session(db, sid, cert_fp=cert_fp)
    if not loaded:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired or cert mismatch")
    _, user = loaded
    request.state.user = user
    request.state.cert_fp = cert_fp
    return user


def require_role(*roles: Role):
    allowed = {r.value for r in roles}

    async def _dep(user: User = Depends(current_user)) -> User:
        if user.role.value not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return _dep
