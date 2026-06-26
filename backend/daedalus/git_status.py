"""Git working-tree status for project workspaces.

The dashboard surfaces "you're N commits behind, run `git pull` before
launching agents" so the user doesn't kick off agents against a stale tree.
A 60s Redis cache avoids hammering the network on every refresh.

This module is shared between:
  * the read endpoints (GET /api/v1/projects/{id}/git-status)
  * the enqueue gate that blocks task runs when behind > 0

`git fetch` is a network call; it has a hard timeout and we never error the
caller on a fetch failure — we just mark `fetch_failed=True` and proceed
with whatever local refs we already have, so a flaky network doesn't
permanently block work.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import structlog

from daedalus.db.redis import get_redis

logger = structlog.get_logger()


# How long we trust a cached git-status entry without re-fetching.
DEFAULT_CACHE_TTL_SECONDS = 60
# `git fetch` hard timeout — workspace remotes are usually local LAN, but
# we don't want to hang a request behind a stuck SSH handshake.
FETCH_TIMEOUT_SECONDS = 15
# `git rev-list` is fast; this guards against unexpected hangs.
REVLIST_TIMEOUT_SECONDS = 10

CACHE_KEY_PREFIX = "daedalus:git_status"


@dataclass
class GitStatus:
    """Result shape returned to the API + cached in Redis."""

    is_git_repo: bool = False
    has_remote: bool = False
    behind_count: int = 0
    ahead_count: int = 0
    branch: str | None = None
    upstream: str | None = None
    fetch_failed: bool = False
    fetch_error: str | None = None
    last_fetched_at: str | None = None
    checked_at: str | None = None
    error: str | None = None

    def needs_pull(self) -> bool:
        return self.behind_count > 0


def _cache_key(project_id: str) -> str:
    return f"{CACHE_KEY_PREFIX}:{project_id}"


async def _run_git(args: list[str], cwd: str, timeout: float) -> tuple[int, str, str]:
    """Run a git command, returning (exit_code, stdout, stderr).

    Never raises — a failed git command produces a (nonzero, "", err) tuple
    and a hung command produces (-1, partial-stdout, "timeout").
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
    except FileNotFoundError:
        return (-1, "", "git binary not found")
    except Exception as e:
        return (-1, "", f"spawn error: {e!r}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            proc.kill()
        except Exception:
            logger.debug("git_status.proc_kill_failed", exc_info=True)
        return (-1, "", f"timeout after {timeout:.1f}s")
    rc = proc.returncode if proc.returncode is not None else -1
    return (rc, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"))


async def _local_status(workspace_path: str) -> GitStatus:
    """Compute behind/ahead from local refs only — no network. Falls back
    cleanly when there's no upstream configured."""
    status = GitStatus(checked_at=datetime.now(UTC).isoformat())
    if not workspace_path or not os.path.isdir(workspace_path):
        status.error = "workspace path missing"
        return status

    rc, _, _ = await _run_git(
        ["rev-parse", "--git-dir"], workspace_path, REVLIST_TIMEOUT_SECONDS
    )
    if rc != 0:
        return status  # not a git repo
    status.is_git_repo = True

    rc, branch, _ = await _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], workspace_path, REVLIST_TIMEOUT_SECONDS
    )
    if rc == 0:
        status.branch = branch.strip() or None

    rc, upstream, _ = await _run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        workspace_path,
        REVLIST_TIMEOUT_SECONDS,
    )
    if rc != 0:
        # No upstream configured — no notion of "behind".
        return status

    status.upstream = upstream.strip() or None
    rc, has_remote_out, _ = await _run_git(
        ["remote"], workspace_path, REVLIST_TIMEOUT_SECONDS
    )
    if rc == 0 and has_remote_out.strip():
        status.has_remote = True

    rc, behind_ahead, _ = await _run_git(
        ["rev-list", "--left-right", "--count", "@{u}...HEAD"],
        workspace_path,
        REVLIST_TIMEOUT_SECONDS,
    )
    if rc == 0:
        parts = behind_ahead.split()
        if len(parts) == 2:
            try:
                status.behind_count = int(parts[0])
                status.ahead_count = int(parts[1])
            except ValueError:
                pass
    return status


async def _fetch_and_status(workspace_path: str) -> GitStatus:
    """Fetch from origin (remote = "origin" if present) then recompute."""
    status = await _local_status(workspace_path)
    if not status.is_git_repo or not status.has_remote:
        return status

    rc, _, err = await _run_git(
        ["fetch", "--quiet"], workspace_path, FETCH_TIMEOUT_SECONDS
    )
    if rc != 0:
        status.fetch_failed = True
        status.fetch_error = err.strip()[:200] or f"git fetch returned {rc}"
        return status
    status.last_fetched_at = datetime.now(UTC).isoformat()

    # After fetching, recompute behind/ahead from the fresh refs.
    refreshed = await _local_status(workspace_path)
    refreshed.last_fetched_at = status.last_fetched_at
    return refreshed


async def get_status(
    project_id: str,
    workspace_path: str,
    *,
    refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> GitStatus:
    """Return git status for a project, hitting the network only on a cache
    miss or when `refresh=True`."""
    redis = get_redis()
    key = _cache_key(project_id)

    if not refresh:
        try:
            raw = await redis.get(key)
        except Exception:
            raw = None
        if raw is not None:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                data = json.loads(raw)
                return GitStatus(**{
                    k: data.get(k) for k in GitStatus.__dataclass_fields__
                })
            except Exception:
                logger.exception("git_status.cache_decode_failed")

    status = await (
        _fetch_and_status(workspace_path) if refresh else _local_status(workspace_path)
    )

    try:
        await redis.set(key, json.dumps(asdict(status)), ex=cache_ttl_seconds)
    except Exception:
        logger.exception("git_status.cache_write_failed", project_id=project_id)
    return status


async def needs_pull(project_id: str, workspace_path: str) -> tuple[bool, GitStatus]:
    """Cheap predicate used by the enqueue gate. Uses the cached status
    when available — does NOT trigger a fresh network fetch."""
    status = await get_status(project_id, workspace_path, refresh=False)
    return (status.needs_pull(), status)
