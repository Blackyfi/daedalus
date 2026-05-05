"""SPA-side diagnostic reports.

The browser POSTs structured diagnostic events here when something goes wrong
the user wouldn't necessarily notice — empty live-runner terminals, dropped
PTY websockets, render-time errors, transcript fetch failures, etc. We
persist them as audit events under action=`ui.<kind>` so they're filterable
on the Audit page.

Lightweight on purpose: a single endpoint, server-side rate-limit, payload
size cap, and structured fields. Don't echo PII (the SPA doesn't send any).
"""
from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user
from daedalus.db.base import get_session
from daedalus.db.models import User
from daedalus.db.redis import get_redis

router = APIRouter()


# Bound the field sizes so a runaway client can't pile garbage into the
# audit log. Anything larger gets clipped server-side.
_MAX_MESSAGE = 1024
_MAX_STACK = 4096
_MAX_CONTEXT_BYTES = 8192


class DiagnosticIn(BaseModel):
    kind: str = Field(..., min_length=1, max_length=80)
    message: str = Field("", max_length=_MAX_MESSAGE)
    run_id: str | None = Field(None, max_length=64)
    project_id: str | None = Field(None, max_length=64)
    url: str | None = Field(None, max_length=512)
    user_agent: str | None = Field(None, max_length=512)
    stack: str | None = Field(None, max_length=_MAX_STACK)
    context: dict[str, Any] = Field(default_factory=dict)


_RATE_LIMIT_PER_MINUTE = 30
_RATE_KEY_PREFIX = "diag:rate"


async def _rate_limit_check(user_id: str) -> None:
    """Sliding-window rate limit per user. Lets ~30 reports/min through;
    silently drops the rest with a 429 so misbehaving SPA clients can't
    flood the audit table."""
    redis = get_redis()
    bucket = int(time.time()) // 60
    key = f"{_RATE_KEY_PREFIX}:{user_id}:{bucket}"
    try:
        cnt = await redis.incr(key)
        if cnt == 1:
            await redis.expire(key, 90)
    except Exception:
        return  # Fail open — diagnostics are best-effort.
    if cnt > _RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "diagnostic rate limit")


@router.post("/log", status_code=status.HTTP_204_NO_CONTENT)
async def log_diagnostic(
    body: DiagnosticIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db=Depends(get_session),
) -> None:
    await _rate_limit_check(str(user.id))

    # `kind` becomes the audit action suffix; sanitise to avoid surprising
    # values landing in `action`.
    kind = "".join(c if (c.isalnum() or c in {".", "_", "-"}) else "_" for c in body.kind)
    if len(kind) > 60:
        kind = kind[:60]

    payload: dict[str, Any] = {
        "message": body.message[:_MAX_MESSAGE],
    }
    if body.url:
        payload["url"] = body.url
    if body.user_agent:
        payload["user_agent"] = body.user_agent
    if body.stack:
        payload["stack"] = body.stack[:_MAX_STACK]
    if body.run_id:
        payload["run_id"] = body.run_id
    if body.project_id:
        payload["project_id"] = body.project_id
    if body.context:
        # Bound the context blob.
        import json

        ctx_json = json.dumps(body.context)
        if len(ctx_json) > _MAX_CONTEXT_BYTES:
            payload["context_truncated"] = True
            payload["context"] = ctx_json[:_MAX_CONTEXT_BYTES]
        else:
            payload["context"] = body.context

    await record(
        db,
        actor_user_id=user.id,
        actor_cert_fp=getattr(request.state, "cert_fp", None),
        action=f"ui.{kind}",
        target_kind="run" if body.run_id else ("project" if body.project_id else None),
        target_id=body.run_id or body.project_id,
        payload=payload,
    )
    await db.commit()
