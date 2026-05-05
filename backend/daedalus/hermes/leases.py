"""Project-lease primitives for Hermes.

A project lease is the platform-level guarantee that "only one Daedalus-managed
run per project at a time" — see project-plan.md §6.3. Within a project, runs
serialise; across projects, they go in parallel up to MAX_CONCURRENT_PROJECTS.

Two Redis keys are involved:

- `hermes:project_lease:<project_id>`  string = run_id, EX = wall_clock + grace
- `hermes:active_projects`             SET   of project_ids currently leased

The atomic-claim operation is a Lua script so the cap-check, lease-check,
queue LREM, and lease SET happen as a single Redis transaction. Two workers
racing on the same queue entry will deterministically end with one winner.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import structlog

from daedalus.db.redis import get_redis

logger = structlog.get_logger()


PROJECT_LEASE_KEY_PREFIX = "hermes:project_lease"
ACTIVE_PROJECTS_KEY = "hermes:active_projects"


def project_lease_key(project_id: str | uuid.UUID) -> str:
    return f"{PROJECT_LEASE_KEY_PREFIX}:{project_id}"


# ── Lua: atomic claim ────────────────────────────────────────────────────────
#
# KEYS[1] = queue list (e.g. hermes:queue:default)
# KEYS[2] = project lease key
# KEYS[3] = active projects set
# ARGV[1] = run_id (the lease value)
# ARGV[2] = lease TTL seconds
# ARGV[3] = max_concurrent_projects (string-encoded int)
# ARGV[4] = payload JSON (must match the queue entry exactly)
# ARGV[5] = project_id
#
# Returns:
#   the payload string on success
#   nil on failure (cap reached / project busy / item already claimed)

_CLAIM_LUA = """
local active_count = redis.call('SCARD', KEYS[3])
if active_count >= tonumber(ARGV[3]) then
    return nil
end

if redis.call('EXISTS', KEYS[2]) == 1 then
    return nil
end

local removed = redis.call('LREM', KEYS[1], 1, ARGV[4])
if removed == 0 then
    return nil
end

redis.call('SET', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[2]))
redis.call('SADD', KEYS[3], ARGV[5])
return ARGV[4]
"""


@dataclass
class ClaimAttempt:
    """Result of a single claim attempt."""

    payload: bytes | None  # None if the claim failed

    @property
    def succeeded(self) -> bool:
        return self.payload is not None


async def try_claim(
    queue_key: str,
    project_id: str | uuid.UUID,
    run_id: str | uuid.UUID,
    payload: bytes,
    *,
    max_concurrent_projects: int,
    lease_ttl_seconds: int,
) -> ClaimAttempt:
    """Atomically remove `payload` from `queue_key` and acquire the project lease.

    Returns a `ClaimAttempt` whose `succeeded` flag tells the caller whether to
    proceed with dispatch or skip and re-scan the queue.
    """
    redis = get_redis()
    result = await redis.eval(
        _CLAIM_LUA,
        3,
        queue_key,
        project_lease_key(project_id),
        ACTIVE_PROJECTS_KEY,
        str(run_id),
        str(lease_ttl_seconds),
        str(max_concurrent_projects),
        payload,
        str(project_id),
    )
    if result is None:
        return ClaimAttempt(payload=None)
    if isinstance(result, str):
        result = result.encode("utf-8")
    return ClaimAttempt(payload=result)


async def release_lease(project_id: str | uuid.UUID) -> None:
    """Release the project's lease and drop it from the active set.

    Called by the dispatcher's completion path. Idempotent — if the lease
    expired or was already released, the DEL/SREM are no-ops.
    """
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.delete(project_lease_key(project_id))
    pipe.srem(ACTIVE_PROJECTS_KEY, str(project_id))
    try:
        await pipe.execute()
    except Exception:
        logger.exception("project_lease_release_failed", project_id=str(project_id))


async def heartbeat(project_id: str | uuid.UUID, ttl_seconds: int) -> bool:
    """Refresh the lease's TTL while a long run is still in flight.

    Returns False if the lease has already expired (caller should treat this
    as an orphan condition: TTL undershot the actual run length, the run is
    still alive in Talos, and concurrent claims may have already started for
    this project — log loudly and abort the run).
    """
    redis = get_redis()
    res = await redis.expire(project_lease_key(project_id), ttl_seconds)
    return bool(res)


async def is_project_busy(project_id: str | uuid.UUID) -> bool:
    """Quick check used by the queue scanner before invoking the Lua script."""
    redis = get_redis()
    return bool(await redis.exists(project_lease_key(project_id)))


async def active_project_count() -> int:
    redis = get_redis()
    return int(await redis.scard(ACTIVE_PROJECTS_KEY))


async def active_projects() -> set[str]:
    """Return the IDs of every project currently holding a lease."""
    redis = get_redis()
    members = await redis.smembers(ACTIVE_PROJECTS_KEY)
    out: set[str] = set()
    for m in members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", errors="replace")
        out.add(m)
    return out


async def clear_stale_leases(live_run_ids: set[str]) -> int:
    """Drop active-set entries whose lease key no longer exists, and (defensive)
    drop entries whose stored run_id isn't in `live_run_ids`.

    Returns the number of stale entries removed. Used by the orphan reclaim
    pass after a Hermes restart.
    """
    redis = get_redis()
    members = await active_projects()
    removed = 0
    for project_id in members:
        key = project_lease_key(project_id)
        val = await redis.get(key)
        if val is None:
            await redis.srem(ACTIVE_PROJECTS_KEY, project_id)
            removed += 1
            continue
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        if val not in live_run_ids:
            await redis.delete(key)
            await redis.srem(ACTIVE_PROJECTS_KEY, project_id)
            removed += 1
    return removed


def parse_payload(raw: bytes | str) -> dict | None:
    """Defensive payload parser for queue scanning.

    The scheduler peeks at queue entries with LRANGE; entries are JSON encoded
    by HermesClient. We need project_id to know whether the project is busy.
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
