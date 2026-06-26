"""Hermes scheduler — per-project concurrent dispatcher.

`MAX_CONCURRENT_PROJECTS` worker coroutines run in parallel inside the
scheduler process. Each one scans the queue lanes (via LRANGE — no churn
from pop-and-repush) and atomically claims the first job belonging to an
idle project (Lua script in `daedalus.hermes.leases`).

A separate bookkeeper coroutine handles orphan reclaim, queue-depth metrics,
and (when this process holds the role) Pythia subscription refresh ticks.

See project-plan.md §6.3 / §6.3.1 / §6.3.2 for the contract this implements.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from daedalus import anomaly, notify
from daedalus.argus import verify_run as argus_verify_run
from daedalus.argus.verifier import (
    ArgusVerdict,
    WorktreeUnreadableError,
    collect_diff,
    extract_agent_final_text,
)
from daedalus.connectors.overrides import resolve as resolve_effective_settings
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import (
    ArgusReport,
    Connector,
    PriorityLane,
    Project,
    Run,
    RunKind,
    RunState,
    Task,
    TaskPriority,
    TaskStatus,
    Verdict,
)
from daedalus.db.redis import get_redis
from daedalus.hermes.client import _QUEUE_PREFIX, HermesClient, _EnqueuePayload
from daedalus.hermes.leases import (
    active_project_count,
    active_projects,
    clear_stale_leases,
    heartbeat,
    parse_payload,
    release_lease,
    try_claim,
)
from daedalus.observability import (
    ARGUS_VERDICTS_TOTAL,
    QUEUE_DEPTH,
    RUN_DURATION_SECONDS,
    RUNS_COMPLETED_TOTAL,
)
from daedalus.storage.objects import get_object_store

logger = structlog.get_logger()

# ── constants ───────────────────────────────────────────────────────────────

# Per-run lock TTL (seconds). Used as a fallback safety net on top of the
# project lease — see _claim_run / orphan reclaim.
_LOCK_TTL_RUNNING = 90

# Match SHA-like identifiers an agent's final report claims as commits — e.g.
# "commit bb3bb18", "committed afd53f3", "Commit: ea32b4e1234". Used by the
# phantom-commit guard to short-circuit Argus when the diff is empty AND the
# agent fabricated a commit hash that doesn't resolve in the worktree.
_AGENT_COMMIT_CLAIM_RE = re.compile(
    r"\b(?:commit(?:ted)?)\b[\s:#]*([a-f0-9]{7,40})\b",
    re.IGNORECASE,
)

# Lane priority order — check urgent first, then default, then bg.
_LANE_ORDER = (PriorityLane.urgent, PriorityLane.default, PriorityLane.bg)

# Completed/terminal states from which we can safely advance dependents.
_TERMINAL_STATES = {
    RunState.completed,
    RunState.cancelled,
    RunState.failed,
    RunState.aborted_unsafe,
}


def _connector_wall_clock_minutes(connector_spec: dict[str, Any] | None) -> int:
    if not isinstance(connector_spec, dict):
        return 60
    rl = connector_spec.get("resource_limits") or {}
    try:
        return int(rl.get("wall_clock_minutes") or 60)
    except (TypeError, ValueError):
        return 60


async def _connector_row_for_run(
    session,
    *,
    run: Run | None = None,
    task: Task | None = None,
    project: Project | None = None,
) -> Connector | None:
    """Resolve the live Connector row that governs a run/task/project.

    Looks at the run's snapshot id first, then the task's connector_id, then
    the project's default_connector_id. Returns None if no connector is
    associated. Used by the override-resolution path so we read the *current*
    force/override state, not what was frozen into the snapshot at run-create
    time.
    """
    cid: str | None = None
    if run is not None:
        snap = run.connector_snapshot or {}
        if isinstance(snap, dict):
            cid = snap.get("id")
    if not cid and task is not None:
        cid = task.connector_id
    if not cid and project is not None:
        cid = project.default_connector_id
    if not cid:
        return None
    res = await session.execute(select(Connector).where(Connector.connector_id == cid))
    return res.scalar_one_or_none()


class HermesScheduler:
    """Multi-worker scheduler with per-project concurrency.

    - N worker coroutines (N = settings.max_concurrent_projects) each loop:
      claim → dispatch → wait_for_completion → release_lease.
    - 1 bookkeeper coroutine: orphan reclaim + metrics.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.redis = get_redis()
        self._stopping = False
        # Throttles low-frequency bookkeeper subtasks (worktree prune etc.).
        # Keyed by subtask name, value is monotonic-time of last successful run.
        self._last_periodic: dict[str, float] = {}

    async def run(self) -> None:
        logger.info(
            "hermes_scheduler_start",
            role=self.settings.role,
            max_concurrent_projects=self.settings.max_concurrent_projects,
        )

        # Initial orphan reclaim before workers spin up so they don't claim
        # against stale leases left over from a previous Hermes process.
        await self._reclaim_orphans()

        worker_tasks = [
            asyncio.create_task(self._worker_loop(i), name=f"hermes-worker-{i}")
            for i in range(self.settings.max_concurrent_projects)
        ]
        bookkeeper = asyncio.create_task(self._bookkeeper_loop(), name="hermes-bookkeeper")

        try:
            await asyncio.gather(*worker_tasks, bookkeeper)
        except asyncio.CancelledError:
            logger.info("hermes_scheduler_cancelled")
        except KeyboardInterrupt:
            logger.info("hermes_scheduler_interrupt")

    # ── worker loop ───────────────────────────────────────────────────────

    async def _worker_loop(self, worker_id: int) -> None:
        """One worker. Tries to claim a runnable job each tick."""
        log = logger.bind(worker=worker_id)
        while not self._stopping:
            try:
                claimed = await self._try_claim_idle_project_job()
            except Exception:
                log.exception("worker_claim_error")
                claimed = None

            if claimed is None:
                await asyncio.sleep(self.settings.scheduler_poll_seconds)
                continue

            run, _payload = claimed
            project_id = str(run.project_id)
            try:
                await self._handle_run(run)
            except Exception:
                log.exception("worker_handle_failed", run_id=str(run.id))
            finally:
                await release_lease(project_id)

    async def _bookkeeper_loop(self) -> None:
        """Periodic housekeeping: orphan reclaim + queue-depth metrics +
        low-frequency hygiene like git-worktree pruning."""
        while not self._stopping:
            try:
                await self._reclaim_orphans()
                for lane in _LANE_ORDER:
                    QUEUE_DEPTH.labels(lane=lane.value).set(
                        int(await self.redis.llen(f"{_QUEUE_PREFIX}:{lane.value}"))
                    )
                # Low-frequency hygiene. Each subtask is throttled internally
                # so we can call it from the every-5s loop without amplifying.
                await self._maybe_prune_worktrees()
                await self._maybe_detect_anomalies()
            except Exception:
                logger.exception("bookkeeper_tick_failed")
            await asyncio.sleep(5)

    async def _maybe_prune_worktrees(self) -> None:
        """Run `git worktree prune` once per project, every ~5 minutes.

        Talos creates a worktree per task run but doesn't always tear them
        down (failed/aborted runs leave orphans; successful runs are kept
        until the merge ship cleans them up). The physical dirs eventually
        get removed by ship.py or operator cleanup, but git's
        `.git/worktrees/<id>/` admin entries linger as `prunable` until
        somebody runs `git worktree prune`. Doing it from a single place
        on a fixed cadence prevents that admin set from growing unbounded.
        """
        interval = float(self.settings.worktree_prune_interval_seconds)
        last = self._last_periodic.get("worktree_prune", 0.0)
        now = time.monotonic()
        if now - last < interval:
            return
        # Optimistically mark "ran" so a failure here doesn't make us spin
        # at the every-5s cadence retrying. Worst case we miss a prune
        # cycle; the next one will catch any orphans.
        self._last_periodic["worktree_prune"] = now

        async for session in get_session():
            res = await session.execute(select(Project.workspace_path))
            paths = [p for (p,) in res.all() if p]
            break
        else:
            return

        pruned = 0
        for ws in paths:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "worktree", "prune",
                    cwd=ws,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0:
                    pruned += 1
                else:
                    logger.warning(
                        "worktree_prune_failed",
                        workspace=ws,
                        rc=proc.returncode,
                        stderr=err.decode(errors="replace").strip()[:200],
                    )
            except (TimeoutError, FileNotFoundError, OSError) as exc:
                logger.warning("worktree_prune_exec_error", workspace=ws, error=str(exc))
        if pruned:
            logger.info("worktree_prune_complete", projects=pruned)

    async def _maybe_detect_anomalies(self) -> None:
        """Scan the recent audit window for anomalies once per interval.

        Mirrors the worktree-prune throttle: called from the every-5s loop but
        only actually runs every `anomaly_scan_interval_seconds`. Records any
        fresh hits (cooldown-gated) as `anomaly.detected` audit events. We mark
        "ran" before the work so a failure doesn't make us retry at the 5s
        cadence — the next interval will catch a standing condition anyway.
        """
        if not self.settings.anomaly_detection_enabled:
            return
        interval = float(self.settings.anomaly_scan_interval_seconds)
        last = self._last_periodic.get("anomaly_scan", 0.0)
        now = time.monotonic()
        if now - last < interval:
            return
        self._last_periodic["anomaly_scan"] = now

        async for session in get_session():
            fired = await anomaly.scan(session, self.redis, self.settings)
            if fired:
                await session.commit()
            break

    # ── orphan recovery ───────────────────────────────────────────────────

    async def _reclaim_orphans(self) -> None:
        """Two-pass orphan recovery.

        Pass 1: Run rows in running/claimed state whose `hermes:lock:<id>`
                is gone are stranded — they were running when something died.
                Mark them aborted_unsafe.
        Pass 2: Stale entries in `hermes:active_projects` whose lease key has
                expired (or whose stored run_id is no longer alive) are dropped
                from the active-set so the cap reflects reality.
        """
        live_run_ids: set[str] = set()
        recovered: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        try:
            async for session in get_session():
                client = HermesClient(session)
                res = await session.execute(
                    select(Run).where(
                        Run.state.in_((RunState.running, RunState.claimed))
                    )
                )
                stale = []
                completed_via_orphan = []  # runs whose Talos finished cleanly
                for run in res.scalars().all():
                    lock_key = f"hermes:lock:{run.id}"
                    ttl = await self.redis.ttl(lock_key)
                    if ttl != -2:
                        live_run_ids.add(str(run.id))
                        continue
                    # Lock is gone. Two sub-cases:
                    #  (a) Talos finished cleanly and dropped the lock; if
                    #      _handle_run hasn't committed yet, leave it alone
                    #      for one bookkeeper tick (≤5s). If it persists past
                    #      that, the wait-loop is gone (Hermes restarted) —
                    #      consume the completion ourselves.
                    #  (b) No completion key → truly stranded; reclaim as
                    #      aborted_unsafe.
                    completion_key = f"hermes:completion:{run.id}"
                    raw = await self.redis.get(completion_key)
                    if raw is not None:
                        # Has the run row been "running" for long enough that
                        # we're confident no in-process wait-loop will pick it
                        # up? Use started_at + 30s as the lower bound; that's
                        # ~6 bookkeeper ticks of grace.
                        started = run.started_at
                        if started is not None and (
                            datetime.now(UTC) - started
                        ).total_seconds() < 30:
                            live_run_ids.add(str(run.id))
                            continue
                        try:
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8")
                            data = json.loads(raw)
                        except Exception:
                            data = {}
                        state_map = {
                            "completed": RunState.completed,
                            "failed": RunState.failed,
                            "cancelled": RunState.cancelled,
                            "aborted_unsafe": RunState.aborted_unsafe,
                        }
                        run.state = state_map.get(
                            data.get("state", "completed"), RunState.completed
                        )
                        run.exit_code = data.get("exit_code")
                        run.finished_at = datetime.now(UTC)
                        run.transcript_object_key = data.get("transcript_object_key")
                        if data.get("token_input") is not None:
                            run.token_input = int(data["token_input"])
                        if data.get("token_output") is not None:
                            run.token_output = int(data["token_output"])
                        if data.get("cost_usd_micros") is not None:
                            run.cost_usd_micros = int(data["cost_usd_micros"])
                        completed_via_orphan.append(run)
                        # Best-effort: drop the completion key now that we've
                        # consumed it, so a subsequent tick doesn't re-run
                        # postprocess.
                        try:
                            await self.redis.delete(completion_key)
                        except Exception:
                            logger.debug(
                                "orphan_completion_key_delete_failed",
                                run_id=str(run.id),
                            )
                        continue
                    run.state = RunState.aborted_unsafe
                    run.finished_at = datetime.now(UTC)
                    run.exit_code = -1
                    stale.append(run)
                if stale or completed_via_orphan:
                    # Mirror the non-completed path of _handle_run for fully-
                    # stranded runs: flip the parent task to needs_fixes.
                    # Skip _postprocess_terminal_run for argus-kind stranded
                    # runs because that path would call the verifier model
                    # against an empty transcript — needs_fixes is right.
                    for run in stale:
                        if run.task_id is not None:
                            task = await session.get(Task, run.task_id)
                            if task is not None and task.status not in (
                                TaskStatus.done,
                                TaskStatus.cancelled,
                            ):
                                task.status = TaskStatus.needs_fixes
                    # For runs whose completion we just consumed (Talos
                    # finished, but the wait-loop is gone), run the full
                    # post-processing — this advances the task to done /
                    # needs_fixes / verifying and unblocks dependents.
                    for run in completed_via_orphan:
                        try:
                            await self._postprocess_terminal_run(session, client, run)
                        except Exception:
                            logger.exception(
                                "orphan_postprocess_failed", run_id=str(run.id)
                            )
                    await session.commit()
                    for run in stale + completed_via_orphan:
                        recovered.append((run.id, run.project_id, run.state.value))
                    for run in stale:
                        logger.warning("orphan_recovered", run_id=str(run.id))
                    for run in completed_via_orphan:
                        logger.info(
                            "orphan_completion_consumed",
                            run_id=str(run.id),
                            state=run.state.value,
                        )
                else:
                    await session.rollback()
                # Publish completion events so subscribers (Iris → frontend)
                # see the state transition without waiting on poll fallback.
                for run_id, project_id, state in recovered:
                    try:
                        await client.publish_project_event(
                            project_id,
                            {
                                "kind": "run.completed",
                                "run_id": str(run_id),
                                "state": state,
                                "exit_code": -1,
                            },
                        )
                        await client.publish_queue_event(
                            {"kind": "completed", "run_id": str(run_id), "state": state}
                        )
                    except Exception:
                        logger.exception(
                            "orphan_recovery_publish_failed", run_id=str(run_id)
                        )
                break
        except Exception:
            logger.exception("orphan_recovery_failed")

        try:
            removed = await clear_stale_leases(live_run_ids)
            if removed:
                logger.info("stale_leases_cleared", count=removed)
        except Exception:
            logger.exception("clear_stale_leases_failed")

    # ── claim ─────────────────────────────────────────────────────────────

    async def _try_claim_idle_project_job(self) -> tuple[Run, _EnqueuePayload] | None:
        """Scan lanes (LRANGE — no dequeue) for the first job belonging to an
        idle project whose deps are met. Atomically claim it via the Lua script.

        Returns (run, payload) on success or None.
        """
        if await active_project_count() >= self.settings.max_concurrent_projects:
            return None

        busy = await active_projects()

        for lane in _LANE_ORDER:
            queue_key = f"{_QUEUE_PREFIX}:{lane.value}"
            entries = await self.redis.lrange(queue_key, 0, -1)
            for raw in entries:
                if isinstance(raw, str):
                    raw_bytes = raw.encode("utf-8")
                else:
                    raw_bytes = raw
                parsed = parse_payload(raw_bytes)
                if parsed is None:
                    continue
                pid = parsed.get("project_id")
                rid_str = parsed.get("run_id")
                if not pid or not rid_str:
                    continue
                if pid in busy:
                    continue

                run_id = uuid.UUID(rid_str)
                if not await self._is_claimable(run_id):
                    continue

                connector_spec = await self._connector_spec_for_run(run_id)
                # Skip if this run's connector is paused due to a recent
                # rate-limit hit. The Redis key has TTL = seconds-until-
                # reset; once it expires we naturally pick the run up on
                # a subsequent worker-loop tick.
                connector_id = (
                    connector_spec.get("id") if isinstance(connector_spec, dict) else None
                )
                if connector_id and await self._is_connector_paused(connector_id):
                    continue
                wall_clock_minutes = _connector_wall_clock_minutes(connector_spec)
                lease_ttl = wall_clock_minutes * 60 + self.settings.project_lease_grace_seconds

                attempt = await try_claim(
                    queue_key=queue_key,
                    project_id=pid,
                    run_id=run_id,
                    payload=raw_bytes,
                    max_concurrent_projects=self.settings.max_concurrent_projects,
                    lease_ttl_seconds=lease_ttl,
                )
                if not attempt.succeeded:
                    # Cap, lease, or LREM lost the race — re-scan from the top.
                    busy = await active_projects()
                    if len(busy) >= self.settings.max_concurrent_projects:
                        return None
                    continue

                run = await self._mark_running(run_id, lease_ttl)
                if run is None:
                    # DB transition lost — release the lease and skip.
                    await release_lease(pid)
                    continue

                try:
                    payload = _EnqueuePayload.from_json(raw_bytes)
                except Exception:
                    payload = None  # type: ignore[assignment]
                return (run, payload)  # type: ignore[return-value]

        return None

    async def _is_claimable(self, run_id: uuid.UUID) -> bool:
        try:
            async for session in get_session():
                client = HermesClient(session)
                res = await session.execute(select(Run).where(Run.id == run_id))
                run = res.scalar_one_or_none()
                if run is None or run.state != RunState.queued:
                    return False
                return await client.run_dependencies_met(run)
        except Exception:
            logger.exception("claim_check_failed", run_id=str(run_id))
            return False
        return False

    async def _connector_spec_for_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        try:
            async for session in get_session():
                run = await session.get(Run, run_id)
                if run is None:
                    return {}
                if run.connector_snapshot:
                    return dict(run.connector_snapshot)
                return {}
        except Exception:
            logger.exception("connector_spec_lookup_failed", run_id=str(run_id))
            return {}
        return {}

    async def _mark_running(self, run_id: uuid.UUID, lease_ttl: int) -> Run | None:
        try:
            async for session in get_session():
                client = HermesClient(session)
                res = await session.execute(
                    select(Run).where(Run.id == run_id, Run.state == RunState.queued)
                )
                run = res.scalar_one_or_none()
                if run is None:
                    return None

                run.state = RunState.running
                run.started_at = datetime.now(UTC)
                if run.kind == RunKind.task and run.task_id is not None:
                    task = await session.get(Task, run.task_id)
                    if task is not None:
                        task.status = TaskStatus.in_progress

                # Per-run lock — used by orphan reclaim to find stranded rows.
                # The value encodes the run kind so a Talos process restarting
                # can identify *its* locks (task vs argus) and drop them on
                # startup, letting Hermes' bookkeeper reclaim cleanly. Format:
                # "<kind>:<rid>" — kept simple so legacy code that only reads
                # TTL still works.
                lock_key = f"hermes:lock:{run.id}"
                lock_value = f"{run.kind.value}:{run.id}"
                await self.redis.set(lock_key, lock_value, ex=max(_LOCK_TTL_RUNNING, lease_ttl), nx=True)

                await session.commit()
                logger.info("run_claimed", run_id=str(run.id), project_id=str(run.project_id))
                await client.publish_project_event(
                    run.project_id,
                    {"kind": "run.claimed", "run_id": str(run.id)},
                )
                await client.publish_queue_event(
                    {"kind": "claimed", "run_id": str(run.id), "lane": run.lane.value}
                )
                return run
        except Exception:
            logger.exception("mark_running_failed", run_id=str(run_id))
            return None
        return None

    # ── per-run handling ──────────────────────────────────────────────────

    async def _handle_run(self, run: Run) -> None:
        """Dispatch + wait + persist completion."""
        exit_code: int | None = None
        final_state: RunState | None = None
        completion_meta: dict[str, Any] = {}

        try:
            final_state, exit_code, completion_meta = await self._dispatch(run)
        except Exception:
            logger.exception("dispatch_failed", run_id=str(run.id))
            final_state = RunState.failed
            exit_code = 1

        try:
            async for session in get_session():
                client = HermesClient(session)
                res = await session.execute(select(Run).where(Run.id == run.id))
                db_run = res.scalar_one_or_none()
                if db_run:
                    db_run.state = final_state or RunState.completed
                    db_run.exit_code = exit_code
                    db_run.finished_at = datetime.now(UTC)
                    db_run.transcript_object_key = completion_meta.get("transcript_object_key")
                    if completion_meta.get("token_input") is not None:
                        db_run.token_input = int(completion_meta["token_input"])
                    if completion_meta.get("token_output") is not None:
                        db_run.token_output = int(completion_meta["token_output"])
                    if completion_meta.get("cost_usd_micros") is not None:
                        db_run.cost_usd_micros = int(completion_meta["cost_usd_micros"])
                    # Rate-limit annotation: if Talos detected a Claude
                    # `rate_limit_event` with status="rejected", flag the
                    # run AND pause every project sharing this connector
                    # via Redis (TTL = seconds until reset). Subsequent
                    # claim attempts skip those projects so we don't burn
                    # through the whole queue against a wall.
                    if completion_meta.get("rate_limited"):
                        db_run.was_rate_limited = True
                        retry_iso = completion_meta.get("retry_after_iso")
                        if retry_iso:
                            try:
                                retry_dt = datetime.fromisoformat(retry_iso)
                                db_run.retry_after = retry_dt
                                await self._pause_connector_for_run(db_run, retry_dt)
                            except (TypeError, ValueError):
                                logger.warning(
                                    "rate_limit_bad_retry_after",
                                    run_id=str(db_run.id),
                                    value=retry_iso,
                                )
                    if db_run.started_at is not None:
                        duration = (db_run.finished_at - db_run.started_at).total_seconds()
                        RUN_DURATION_SECONDS.labels(kind=db_run.kind.value).observe(duration)
                    RUNS_COMPLETED_TOTAL.labels(
                        kind=db_run.kind.value, state=db_run.state.value
                    ).inc()
                    await self._postprocess_terminal_run(session, client, db_run)
                    await session.commit()
                    await client.publish_project_event(
                        db_run.project_id,
                        {
                            "kind": "run.completed",
                            "run_id": str(db_run.id),
                            "state": db_run.state.value,
                            "exit_code": db_run.exit_code,
                        },
                    )
                    await client.publish_queue_event(
                        {"kind": "completed", "run_id": str(db_run.id), "state": db_run.state.value}
                    )
                break
        except Exception:
            logger.exception("completion_update_failed", run_id=str(run.id))

    # ── dispatch ──────────────────────────────────────────────────────────

    async def _dispatch(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        if run.kind == RunKind.task:
            return await self._dispatch_task(run)
        if run.kind == RunKind.argus:
            return await self._dispatch_argus(run)
        if run.kind == RunKind.planning:
            return await self._dispatch_planning(run)
        if run.kind == RunKind.cleanup:
            return await self._dispatch_cleanup(run)
        logger.error("unknown_kind", kind=run.kind.value, run_id=str(run.id))
        return (RunState.failed, 1, {})

    async def _dispatch_task(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        payload = await self._build_run_signal_payload(run, action="run")
        await self.redis.publish(f"hermes:signal:{run.id}", json.dumps(payload))
        logger.info("dispatched_task", run_id=str(run.id))
        return await self._wait_for_completion(run)

    async def _dispatch_argus(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        payload = await self._build_run_signal_payload(run, action="argus_verify")
        await self.redis.publish(f"hermes:signal:{run.id}", json.dumps(payload))
        logger.info("dispatched_argus", run_id=str(run.id))
        return await self._wait_for_completion(run)

    async def _dispatch_planning(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        internal_url = f"{self.settings.internal_api_base.rstrip('/')}/api/internal/planning/generate"
        payload = {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "action": "generate_tasks",
        }
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    internal_url,
                    json=payload,
                    headers={"X-Daedalus-Internal-Key": self.settings.internal_key},
                )
                if resp.status_code >= 400:
                    logger.error(
                        "planning_api_error",
                        run_id=str(run.id),
                        status=resp.status_code,
                        body=resp.text,
                    )
                    return (RunState.failed, resp.status_code, {})
                return (RunState.completed, 0, {})
        except Exception:
            logger.exception("planning_dispatch_failed", run_id=str(run.id))
            return (RunState.failed, 1, {})

    async def _dispatch_cleanup(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        return (RunState.completed, 0, {})

    async def _wait_for_completion(self, run: Run) -> tuple[RunState, int | None, dict[str, Any]]:
        """Poll for Talos completion signal with a hard cap. Heartbeats the
        project lease so it never expires while the run is alive."""
        connector = run.connector_snapshot or {}
        wall_clock = _connector_wall_clock_minutes(connector) * 60
        # +5 min slack so the wait is strictly longer than what Talos itself enforces.
        timeout_seconds = wall_clock + 300

        completion_key = f"hermes:completion:{run.id}"
        lease_ttl = wall_clock + self.settings.project_lease_grace_seconds
        deadline = time.monotonic() + timeout_seconds
        last_heartbeat = time.monotonic()

        while time.monotonic() < deadline:
            raw = await self.redis.get(completion_key)
            if raw is not None:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    data = json.loads(raw)
                    state_map = {
                        "completed": RunState.completed,
                        "failed": RunState.failed,
                        "cancelled": RunState.cancelled,
                        "aborted_unsafe": RunState.aborted_unsafe,
                    }
                    final_state = state_map.get(data.get("state", "completed"), RunState.completed)
                    logger.info(
                        "completion_received",
                        run_id=str(run.id),
                        state=final_state.value,
                        exit_code=data.get("exit_code"),
                    )
                    return (final_state, data.get("exit_code"), data)
                except Exception:
                    logger.warning("bad_completion_payload", run_id=str(run.id), raw=raw)

            now = time.monotonic()
            if now - last_heartbeat >= self.settings.project_lease_heartbeat_seconds:
                ok = await heartbeat(run.project_id, lease_ttl)
                if not ok:
                    # Lease vanished — the project is no longer protected, so
                    # tell Talos to kill the run before another worker claims
                    # the same project. This guards against the unlikely case
                    # where wall_clock + grace was undershot.
                    logger.error(
                        "project_lease_lost", run_id=str(run.id), project_id=str(run.project_id)
                    )
                    try:
                        await self.redis.publish(
                            f"hermes:signal:{run.id}",
                            json.dumps({"run_id": str(run.id), "action": "kill"}),
                        )
                    except Exception:
                        logger.exception("project_lease_lost_kill_publish_failed", run_id=str(run.id))
                    return (RunState.aborted_unsafe, None, {})
                last_heartbeat = now

            await asyncio.sleep(1)

        logger.error("run_timeout", run_id=str(run.id))
        return (RunState.aborted_unsafe, None, {})

    # ── payload assembly ──────────────────────────────────────────────────

    async def _build_run_signal_payload(self, run: Run, *, action: str) -> dict[str, Any]:
        task: Task | None = None
        project: Project | None = None
        connector: Connector | None = None
        connector_spec = run.connector_snapshot or {}

        async for session in get_session():
            if run.task_id is not None:
                task = await session.get(Task, run.task_id)
            project = await session.get(Project, run.project_id)
            connector = await _connector_row_for_run(
                session, run=run, task=task, project=project
            )
            if not connector_spec and connector is not None:
                connector_spec = connector.spec
            break

        effective = resolve_effective_settings(project, connector)

        resource_limits = dict(connector_spec.get("resource_limits", {}))
        if effective.wall_clock_minutes is not None:
            resource_limits["wall_clock_minutes"] = effective.wall_clock_minutes

        return {
            "run_id": str(run.id),
            "action": action,
            "timestamp": datetime.now(UTC).isoformat(),
            "connector_spec": connector_spec,
            "task": {
                "id": str(task.id) if task else None,
                "title": task.title if task else "",
                "description": task.description if task else "",
                "acceptance_criteria": task.acceptance_criteria if task else "",
                "profile": task.profile if task else "confirm",
            },
            "project": {
                "id": str(project.id) if project else "",
                "name": project.name if project else "",
                "workspace_path": project.workspace_path if project else "",
                "git_default_branch": project.git_default_branch if project else "main",
                "active_worktree_path": run.worktree_path or "",
                "task_model": effective.task_model,
                "verifier_model": effective.verifier_model,
            },
            "resource_limits": resource_limits,
        }

    async def _postprocess_terminal_run(
        self,
        session,
        client: HermesClient,
        run: Run,
    ) -> None:
        task = await session.get(Task, run.task_id) if run.task_id is not None else None
        if task is None:
            return

        if run.kind == RunKind.task:
            if run.was_rate_limited:
                # Don't treat as a task failure — the agent didn't err, the
                # account just ran out of headroom. Reset the task to ready
                # so the scheduler will re-claim it once the connector is
                # un-paused (Redis TTL). No fix-loop.
                task.status = TaskStatus.ready
                return
            if run.state == RunState.completed:
                project_for_argus = await session.get(Project, run.project_id)
                connector_for_argus = await _connector_row_for_run(
                    session, run=run, task=task, project=project_for_argus
                )
                argus_on = resolve_effective_settings(
                    project_for_argus, connector_for_argus
                ).argus_enabled
                wants_verify = (
                    run.connector_snapshot.get("verify_commands")
                    or run.connector_snapshot.get("argus_profile")
                )
                if argus_on and wants_verify:
                    await client.enqueue_argus_verify(run, task)
                else:
                    task.status = TaskStatus.done
                    await client.advance_dependents(run)
            else:
                # Never demote a task that's already settled. Orphan-recovery
                # races can re-postprocess a stale task-kind run AFTER Argus
                # has already passed/cancelled the task — see cdec090d in the
                # needs_fixes audit.
                if task.status not in (TaskStatus.done, TaskStatus.cancelled):
                    task.status = TaskStatus.needs_fixes
                else:
                    logger.info(
                        "postprocess.skip_demotion",
                        run_id=str(run.id),
                        task_id=str(task.id),
                        current_status=task.status.value,
                        run_state=run.state.value,
                    )
            return

        if run.kind != RunKind.argus:
            return

        if run.was_rate_limited:
            # An argus run that hit the rate limit isn't a verification
            # failure. Re-queue argus by flipping the task back to whatever
            # state it should be in to retry verification. Easiest: leave
            # task at `verifying` and let the scheduler re-claim once the
            # connector unpauses; but `verifying` isn't claimable. Reset
            # the parent task to `ready` instead so the FULL task re-runs
            # — we lose the previous task-run's diff, but that's the price
            # of guaranteeing fresh verification post-pause. (Acceptable;
            # argus rate-limits are rare and the worktree is preserved.)
            task.status = TaskStatus.ready
            return

        verify_exit_code = run.exit_code
        verify_output = ""
        if run.transcript_object_key:
            try:
                verify_output = get_object_store().get_text(run.transcript_object_key)
            except Exception:
                logger.warning("argus.transcript_fetch_failed", run_id=str(run.id))

        project = await session.get(Project, task.project_id)
        connector_for_run = await _connector_row_for_run(
            session, run=run, task=task, project=project
        )
        effective = resolve_effective_settings(project, connector_for_run)
        diff_text = ""
        if run.worktree_path and project is not None:
            try:
                diff_text = await collect_diff(run.worktree_path, project.git_default_branch)
            except WorktreeUnreadableError as exc:
                # Infra error — this process can't see the run worktree.
                # Don't write an Argus report (no honest verdict is
                # possible), don't transition the task to needs_fixes
                # (that would punish the agent for an operator problem).
                # Reset the parent task to `ready` so the next run-all
                # picks it up once the infra is fixed.
                logger.error(
                    "argus.diff_unreadable_infra",
                    run_id=str(run.id),
                    task_id=str(task.id),
                    worktree_path=run.worktree_path,
                    error=str(exc),
                    note=(
                        "verifier process cannot read the run worktree — "
                        "check that /workspaces is mounted into this "
                        "container (hermes service in docker-compose)"
                    ),
                )
                task.status = TaskStatus.ready
                return
            except Exception:
                logger.warning("argus.diff_collect_failed", run_id=str(run.id), exc_info=True)

        connector_spec = run.connector_snapshot or {}
        verify_commands = connector_spec.get("verify_commands") or []

        # Analytical tasks (review / audit) often produce no diff — the
        # deliverable lives in the agent's final report. Pull it from the
        # parent task run's transcript so Argus can judge the work, not just
        # the absence of file changes.
        agent_final_text = ""
        if not diff_text.strip() and task is not None:
            agent_final_text = await self._fetch_task_final_report(session, task.id, run.id)

        # Phantom-commit guard: when the diff is empty but the agent's report
        # claims a specific commit, that commit had better exist in the run's
        # worktree. If it doesn't, the LLM verifier sometimes still returns
        # `pass` based on the narrative alone — we observed this masking real
        # failures. Short-circuit to a deterministic fail before the LLM call.
        argus_result: ArgusVerdict | None = None
        if (
            not diff_text.strip()
            and agent_final_text
            and run.worktree_path
        ):
            phantom = await self._detect_phantom_commit(
                run.worktree_path, agent_final_text
            )
            if phantom is not None:
                logger.warning(
                    "argus.phantom_commit_detected",
                    run_id=str(run.id),
                    task_id=str(task.id),
                    phantom_sha=phantom,
                )
                argus_result = ArgusVerdict(
                    verdict="fail",
                    summary=(
                        f"Agent report references commit {phantom} but the diff "
                        f"against the default branch is empty and the SHA does "
                        f"not resolve in the run worktree."
                    ),
                    findings=[
                        {
                            "severity": "blocker",
                            "category": "missing",
                            "description": (
                                "Phantom commit claim — the agent's report cites a "
                                "commit hash that doesn't exist in the repo."
                            ),
                            "evidence": phantom,
                        }
                    ],
                    suggested_fix_task={
                        "title": f"Fix: {task.title}",
                        "description": (
                            "Previous run claimed a commit that does not exist. "
                            "Re-run and ensure all changes are actually committed "
                            "to the run branch before reporting completion."
                        ),
                        "acceptance_criteria": task.acceptance_criteria,
                    },
                )

        if argus_result is None:
            argus_result = await argus_verify_run(
                task_title=task.title,
                task_description=task.description,
                acceptance_criteria=task.acceptance_criteria,
                verify_commands=list(verify_commands),
                diff_text=diff_text,
                verify_output=verify_output,
                verify_exit_code=verify_exit_code,
                verifier_model=effective.verifier_model,
                agent_final_text=agent_final_text,
            )

        verdict_map = {
            "pass": Verdict.pass_,
            "partial": Verdict.partial,
            "fail": Verdict.fail,
        }
        verdict = verdict_map.get(argus_result.verdict, Verdict.fail)
        ARGUS_VERDICTS_TOTAL.labels(verdict=verdict.value).inc()
        passed = verdict == Verdict.pass_
        summary = argus_result.summary or (
            "Verification commands completed." if passed else "Verification failed."
        )
        findings = list(argus_result.findings)
        suggested_fix_task = argus_result.suggested_fix_task

        if passed:
            task.status = TaskStatus.done
        elif task.status in (TaskStatus.done, TaskStatus.cancelled):
            # Same race-guard as the task-kind path above: a stale argus run
            # finishing after the task has already been settled elsewhere
            # must not demote it.
            logger.info(
                "postprocess.skip_demotion",
                run_id=str(run.id),
                task_id=str(task.id),
                current_status=task.status.value,
                verdict=verdict.value,
            )
        elif verdict == Verdict.partial:
            task.status = TaskStatus.needs_fixes
        else:
            task.status = TaskStatus.needs_fixes

        report_res = await session.execute(select(ArgusReport).where(ArgusReport.run_id == run.id))
        report = report_res.scalar_one_or_none()
        if report is None:
            report = ArgusReport(
                run_id=run.id,
                task_id=task.id,
                verdict=verdict,
                summary=summary,
                findings=findings,
                suggested_fix_task=suggested_fix_task,
            )
            session.add(report)
        else:
            report.verdict = verdict
            report.summary = summary
            report.findings = findings
            report.suggested_fix_task = suggested_fix_task

        if passed:
            await client.advance_dependents(run)
            return

        notify.emit(
            "needs_fixes",
            f"Task '{task.title}' did not pass verification ({verdict.value}).",
            task_id=str(task.id),
            project_id=str(task.project_id),
            verdict=verdict.value,
            summary_text=summary,
        )

        diff_hash = await self._compute_diff_hash(run, await session.get(Project, task.project_id))
        if diff_hash and task.last_diff_hash == diff_hash:
            logger.warning(
                "fix_loop_no_progress",
                task_id=str(task.id),
                diff_hash=diff_hash,
            )
            if task.status not in (TaskStatus.done, TaskStatus.cancelled):
                task.status = TaskStatus.needs_fixes
            findings.append(
                {
                    "severity": "blocker",
                    "category": "regression",
                    "description": "Fix loop produced no new changes; halted to avoid runaway.",
                    "evidence": f"diff hash unchanged: {diff_hash}",
                }
            )
            report.findings = findings
            report.summary = (
                f"{summary}\n\nHalted: same diff observed on consecutive verify failures."
            )
            return
        if diff_hash:
            task.last_diff_hash = diff_hash

        task.fix_loop_count += 1
        project = await session.get(Project, task.project_id)
        # Cap fix loops by *chain depth* (walking parent_task_id), not the
        # per-task counter: fix-child rows start at fix_loop_count=0, so the
        # old per-task check let chains grow unbounded. Tag the chain root for
        # manual review and stop spawning once depth >= max_fix_loops.
        chain_depth = await self._fix_chain_depth(session, task)
        if project is not None and chain_depth >= effective.max_fix_loops:
            root = await self._fix_chain_root(session, task)
            if "manual-review" not in root.tags:
                root.tags = list(dict.fromkeys([*root.tags, "manual-review"]))
            return

        fix_task = Task(
            project_id=task.project_id,
            parent_task_id=task.id,
            title=f"Fix: {task.title}",
            description=self._render_fix_description(task, findings),
            acceptance_criteria="Resolve the verification findings and restore passing verification commands.",
            priority=TaskPriority.P1 if task.priority != TaskPriority.P0 else TaskPriority.P0,
            connector_id=task.connector_id,
            profile=task.profile,
            tags=list(dict.fromkeys([*task.tags, "fix-loop"])),
        )
        session.add(fix_task)
        await session.flush()

        if project is not None and project.auto_run_fix:
            await client.enqueue_task(fix_task)

    async def _fix_chain_depth(self, session, task: Task) -> int:
        """Number of fix-loop hops between *task* and its chain root, found by
        walking ``parent_task_id``. A root task (no parent) is depth 0. Stops
        if the parent row is missing (orphaned) and caps at 50 hops so a
        pathological parent cycle can't spin forever."""
        depth = 0
        current = task
        for _ in range(50):
            parent_id = current.parent_task_id
            if parent_id is None:
                break
            parent = await session.get(Task, parent_id)
            if parent is None:
                break
            depth += 1
            current = parent
        return depth

    async def _fix_chain_root(self, session, task: Task) -> Task:
        """Walk ``parent_task_id`` to the original task at the head of the
        chain. Returns *task* itself if it has no resolvable parent. Capped at
        50 hops to stay safe against parent cycles."""
        current = task
        for _ in range(50):
            parent_id = current.parent_task_id
            if parent_id is None:
                break
            parent = await session.get(Task, parent_id)
            if parent is None:
                break
            current = parent
        return current

    async def _fetch_task_final_report(
        self, session, task_id: uuid.UUID, exclude_run_id: uuid.UUID
    ) -> str:
        """Find the most recent task-kind run for this task and return the
        agent's final report extracted from its transcript. Empty string if
        no usable transcript is found."""
        try:
            res = await session.execute(
                select(Run)
                .where(Run.task_id == task_id, Run.kind == RunKind.task, Run.id != exclude_run_id)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
            task_run = res.scalar_one_or_none()
            if task_run is None or not task_run.transcript_object_key:
                return ""
            try:
                transcript = get_object_store().get_text(task_run.transcript_object_key)
            except Exception:
                logger.warning("argus.task_transcript_fetch_failed", run_id=str(task_run.id))
                return ""
            return extract_agent_final_text(transcript)
        except Exception:
            logger.exception("argus.fetch_task_final_report_failed", task_id=str(task_id))
            return ""

    async def _detect_phantom_commit(
        self, worktree_path: str, report_text: str
    ) -> str | None:
        """Return the first commit-SHA the agent's report claims that
        does NOT resolve in the worktree, or None if every claimed SHA is
        real (or the report claims none at all). Caller invokes this only
        when the diff is empty — in that situation a non-resolving SHA is
        an unambiguous lie, regardless of task type.
        """
        if not report_text or not worktree_path:
            return None
        seen: set[str] = set()
        for match in _AGENT_COMMIT_CLAIM_RE.finditer(report_text):
            sha = match.group(1).lower()
            if sha in seen:
                continue
            seen.add(sha)
            if len(seen) > 5:
                break
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "cat-file", "-e", sha,
                    cwd=worktree_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                rc = await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, Exception):
                continue
            if rc != 0:
                return sha
        return None

    async def _compute_diff_hash(self, run: Run, project: Project | None) -> str | None:
        if not run.worktree_path or project is None:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", f"{project.git_default_branch}...HEAD", "--no-color",
                cwd=run.worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            if proc.returncode not in (0, None):
                return None
            return hashlib.sha256(out).hexdigest()
        except Exception:
            logger.exception("diff_hash_failed", run_id=str(run.id))
            return None

    def _render_fix_description(self, task: Task, findings: list[dict[str, Any]]) -> str:
        lines = [f"Follow-up for task: {task.title}", "", "Argus findings:"]
        if not findings:
            lines.append("- Verification failed without structured findings.")
        else:
            for finding in findings:
                lines.append(f"- {finding['description']}")
                if finding.get("evidence"):
                    lines.append(f"  Evidence: {finding['evidence']}")
        return "\n".join(lines)

    # ── rate-limit pause ──────────────────────────────────────────────────

    async def _pause_connector_for_run(self, run: Run, retry_after: datetime) -> None:
        """Set a Redis pause key for the run's connector with TTL =
        seconds-until-reset. While the key exists, no project using this
        connector will have its queued runs claimed.
        """
        spec = run.connector_snapshot or {}
        connector_id = spec.get("id") if isinstance(spec, dict) else None
        if not connector_id:
            logger.warning(
                "rate_limit_pause_no_connector_id", run_id=str(run.id)
            )
            return
        now = datetime.now(UTC)
        ttl = max(60, int((retry_after - now).total_seconds()))
        key = f"daedalus:connector_paused:{connector_id}"
        payload = json.dumps(
            {
                "connector_id": connector_id,
                "run_id": str(run.id),
                "project_id": str(run.project_id),
                "retry_after": retry_after.isoformat(),
                "hit_at": now.isoformat(),
            }
        )
        try:
            await self.redis.set(key, payload, ex=ttl)
            logger.warning(
                "connector_rate_limited",
                connector_id=connector_id,
                run_id=str(run.id),
                retry_after=retry_after.isoformat(),
                ttl_seconds=ttl,
            )
        except Exception:
            logger.exception("connector_pause_redis_failed", run_id=str(run.id))

    async def _is_connector_paused(self, connector_id: str) -> bool:
        """Cheap boolean check: does the rate-limit pause key still exist
        for this connector? Redis returns -2 if the key is gone."""
        try:
            return bool(
                await self.redis.exists(f"daedalus:connector_paused:{connector_id}")
            )
        except Exception:
            logger.exception("connector_pause_check_failed", connector_id=connector_id)
            return False
