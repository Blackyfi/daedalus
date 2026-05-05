"""Iris realtime service — PTY stream + project events + queue events.

All websocket endpoints require:
  * a valid `daedalus_session` cookie (from a completed 3-step login), and
  * the same `X-Client-Cert-Fingerprint` header the API saw at login time
    (forwarded by Caddy from the mTLS handshake).

This is enforced before `accept()` so unauthenticated clients never see
any traffic.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from daedalus.auth.sessions import COOKIE_NAME, unsign_session_id
from daedalus.core.logging import configure_logging
from daedalus.core.settings import get_settings
from daedalus.db.base import dispose_engine, get_engine, get_sessionmaker
from daedalus.db.models import Project, Role, Run, Session as SessionModel, User
from daedalus.db.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    redis = get_redis()
    await redis.ping()
    get_engine()
    yield
    await close_redis()
    await dispose_engine()


app = FastAPI(title="Daedalus Iris", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── auth helpers ─────────────────────────────────────────────────────────────


async def _authenticate(websocket: WebSocket) -> User | None:
    """Validate session cookie + cert fingerprint. Returns the User or None."""
    cookie = websocket.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    cert_fp = websocket.headers.get("x-client-cert-fingerprint")
    settings = get_settings()
    if not cert_fp:
        if settings.require_client_cert:
            return None
        cert_fp = "no-mtls"
    else:
        cert_fp = cert_fp.lower()

    settings = get_settings()
    sid = unsign_session_id(cookie, max_age_seconds=settings.session_hard_hours * 3600)
    if sid is None:
        return None

    sm = get_sessionmaker()
    async with sm() as db:
        stmt = (
            select(SessionModel, User)
            .join(User, User.id == SessionModel.user_id)
            .where(
                SessionModel.id == sid,
                SessionModel.cert_fingerprint == cert_fp,
                SessionModel.revoked_at.is_(None),
            )
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            return None
        return row[1]


async def _user_can_see_run(user: User, run_id: uuid.UUID) -> Run | None:
    """Return the Run if the user is authorised to see it, else None."""
    sm = get_sessionmaker()
    async with sm() as db:
        stmt = (
            select(Run, Project)
            .join(Project, Project.id == Run.project_id)
            .where(Run.id == run_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            return None
        run, proj = row
        if user.role != Role.owner and proj.owner_id != user.id:
            return None
        return run


async def _user_can_see_project(user: User, project_id: uuid.UUID) -> bool:
    sm = get_sessionmaker()
    async with sm() as db:
        proj = await db.get(Project, project_id)
        if proj is None:
            return False
        if user.role == Role.owner:
            return True
        return proj.owner_id == user.id


async def _close_unauthorized(websocket: WebSocket) -> None:
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


# ── PTY stream ───────────────────────────────────────────────────────────────
#
# Message envelope (JSON in both directions).
#
# Server → client:
#   {"t":"data","d":"<utf-8 bytes from the PTY>"}
#   {"t":"state","you_hold_input":bool,"held_by":<email|null>,"has_holder":bool}
#
# Client → server:
#   {"t":"input","d":"<keystrokes>"}      # only the holder is honoured
#   {"t":"takeover"}                       # claim the input role
#   {"t":"release"}                        # voluntarily drop input
#   {"t":"ping"}                           # holder heartbeat (refreshes TTL)
#
# Coordination primitives (per run_id):
#   pty:holder:{rid}  → JSON with the current holder's connection + user info
#                       (TTL 120s, refreshed by holder activity).
#   pty:state:{rid}   → pub/sub channel; every connection re-reads holder
#                       state when it sees a message here.
#
# First connection to attach is auto-promoted to holder. Subsequent ones land
# as read-only viewers and can call {"t":"takeover"} to seize input. The
# previous holder receives an updated state message (their UI shows the toast).

_PTY_HOLDER_TTL = 120


def _holder_key(run_id: str) -> str:
    return f"pty:holder:{run_id}"


def _state_chan(run_id: str) -> str:
    return f"pty:state:{run_id}"


async def _read_pty_holder(redis, key: str) -> dict | None:
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except (json.JSONDecodeError, ValueError):
        return None


async def _set_pty_holder(redis, key: str, conn_id: str, user: User, *, only_if_vacant: bool) -> None:
    payload = json.dumps(
        {
            "connection_id": conn_id,
            "user_id": str(user.id),
            "user_email": user.email,
            "user_display_name": user.display_name,
        }
    )
    if only_if_vacant:
        await redis.set(key, payload, nx=True, ex=_PTY_HOLDER_TTL)
    else:
        await redis.set(key, payload, ex=_PTY_HOLDER_TTL)


async def _release_pty_holder(redis, key: str, chan: str, conn_id: str) -> None:
    cur = await _read_pty_holder(redis, key)
    if cur is None:
        return
    if cur.get("connection_id") != conn_id:
        return
    await redis.delete(key)
    await redis.publish(chan, json.dumps({"kind": "released"}))


async def _send_pty_state(websocket: WebSocket, redis, holder_key: str, conn_id: str) -> None:
    holder = await _read_pty_holder(redis, holder_key)
    you = holder is not None and holder.get("connection_id") == conn_id
    held_by = None
    if holder and not you:
        held_by = holder.get("user_email") or holder.get("user_display_name")
    await websocket.send_text(
        json.dumps(
            {
                "t": "state",
                "you_hold_input": you,
                "held_by": held_by,
                "has_holder": holder is not None,
            }
        )
    )


async def _handle_pty_client_msg(
    raw: str,
    redis,
    holder_key: str,
    state_chan: str,
    conn_id: str,
    user: User,
    run_id: str,
) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return

    kind = msg.get("t")
    if kind == "input":
        cur = await _read_pty_holder(redis, holder_key)
        if cur is None or cur.get("connection_id") != conn_id:
            return
        await redis.expire(holder_key, _PTY_HOLDER_TTL)
        text = msg.get("d", "")
        if text:
            await redis.publish(
                f"hermes:signal:{run_id}",
                json.dumps({"run_id": run_id, "action": "inject", "text": text}),
            )
    elif kind == "takeover":
        await _set_pty_holder(redis, holder_key, conn_id, user, only_if_vacant=False)
        await redis.publish(
            state_chan,
            json.dumps({"kind": "takeover", "by": user.email}),
        )
    elif kind == "release":
        await _release_pty_holder(redis, holder_key, state_chan, conn_id)
    elif kind == "ping":
        cur = await _read_pty_holder(redis, holder_key)
        if cur and cur.get("connection_id") == conn_id:
            await redis.expire(holder_key, _PTY_HOLDER_TTL)


@app.websocket("/ws/pty/{run_id}")
async def pty_stream(websocket: WebSocket, run_id: str) -> None:
    user = await _authenticate(websocket)
    if user is None:
        await _close_unauthorized(websocket)
        return
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        await _close_unauthorized(websocket)
        return
    run = await _user_can_see_run(user, rid)
    if run is None:
        await _close_unauthorized(websocket)
        return

    await websocket.accept()
    redis = get_redis()
    stream_key = f"pty:{run_id}"
    holder_key = _holder_key(run_id)
    state_chan = _state_chan(run_id)
    # Default `0` — replay the full retained PTY buffer (XADD MAXLEN ~10000)
    # on attach so a viewer dropping in mid-run sees the transcript-so-far,
    # not a blank terminal. Clients that already have the tail can pass an
    # explicit `last_id` to resume incrementally.
    last_id = websocket.query_params.get("last_id", "0")
    conn_id = uuid.uuid4().hex

    # First-attached gets input by default. Subsequent attaches stay read-only.
    await _set_pty_holder(redis, holder_key, conn_id, user, only_if_vacant=True)
    await _send_pty_state(websocket, redis, holder_key, conn_id)
    await redis.publish(state_chan, json.dumps({"kind": "join"}))

    state_pubsub = redis.pubsub()
    await state_pubsub.subscribe(state_chan)

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                await _handle_pty_client_msg(
                    raw, redis, holder_key, state_chan, conn_id, user, run_id
                )
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                break

            items = await redis.xread({stream_key: last_id}, block=200, count=100)
            for _, entries in items:
                for entry_id, fields in entries:
                    last_id = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                    raw_field = fields.get(b"data") or fields.get("data")
                    if not raw_field:
                        continue
                    encoded = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
                    text = bytes.fromhex(encoded).decode("utf-8", errors="replace")
                    await websocket.send_text(json.dumps({"t": "data", "d": text}))

            state_msg = await state_pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.05
            )
            if state_msg and state_msg.get("type") == "message":
                await _send_pty_state(websocket, redis, holder_key, conn_id)
    except WebSocketDisconnect:
        pass
    finally:
        await _release_pty_holder(redis, holder_key, state_chan, conn_id)
        try:
            await state_pubsub.unsubscribe(state_chan)
            await state_pubsub.aclose()
        except Exception:
            pass


# ── project events ───────────────────────────────────────────────────────────


@app.websocket("/ws/projects/{project_id}/events")
async def project_events(websocket: WebSocket, project_id: str) -> None:
    user = await _authenticate(websocket)
    if user is None:
        await _close_unauthorized(websocket)
        return
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        await _close_unauthorized(websocket)
        return
    if not await _user_can_see_project(user, pid):
        await _close_unauthorized(websocket)
        return

    await websocket.accept()
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"events:project:{project_id}")

    try:
        while True:
            try:
                _ = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg.get("type") == "message":
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"events:project:{project_id}")
        await pubsub.aclose()


# ── queue events ─────────────────────────────────────────────────────────────


@app.websocket("/ws/queue")
async def queue_events(websocket: WebSocket) -> None:
    user = await _authenticate(websocket)
    if user is None:
        await _close_unauthorized(websocket)
        return

    await websocket.accept()
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("events:queue")

    try:
        while True:
            try:
                _ = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg.get("type") == "message":
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe("events:queue")
        await pubsub.aclose()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.iris_port, log_level="info")


if __name__ == "__main__":
    main()
