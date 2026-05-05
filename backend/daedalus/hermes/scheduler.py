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
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from daedalus.argus import verify_run as argus_verify_run
from daedalus.argus.verifier import collect_diff
from daedalus.core.settings import get_settings
from daedalus.observability import (
    ARGUS_VERDICTS_TOTAL,
    QUEUE_DEPTH,
    RUNS_COMPLETED_TOTAL,
    RUN_DURATION_SECONDS,
)
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
from daedalus.hermes.client import HermesClient, _EnqueuePayload, _QUEUE_PREFIX
from daedalus.hermes.leases import (
    active_project_count,
    active_projects,
    clear_stale_leases,
    heartbeat,
    parse_payload,
    release_lease,
    try_claim,
)
from daedalus.storage.objects import get_object_store

logger = structlog.get_logger()

# ── constants ───────────────────────────────────────────────────────────────

# Per-run lock TTL (seconds). Used as a fallback safety net on top of the
# project lease — see _claim_run / orphan reclaim.
_LOCK_TTL_RUNNING = 90

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

            run, payload = claimed
            project_id = str(run.project_id)
            try:
                await self._handle_run(run)
            except Exception:
                log.exception("worker_handle_failed", run_id=str(run.id))
            finally:
                await release_lease(project_id)

    async def _bookkeeper_loop(self) -> None:
        """Periodic housekeeping: orphan reclaim + queue-depth metrics."""
        while not self._stopping:
            try:
                await self._reclaim_orphans()
                for lane in _LANE_ORDER:
                    QUEUE_DEPTH.labels(lane=lane.value).set(
                        int(await self.redis.llen(f"{_QUEUE_PREFIX}:{lane.value}"))
                    )
            except Exception:
                logger.exception("bookkeeper_tick_failed")
            await asyncio.sleep(5)

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
        try:
            async for session in get_session():
                res = await session.execute(
                    select(Run).where(
                        Run.state.in_((RunState.running, RunState.claimed))
                    )
                )
                stale = []
                for run in res.scalars().all():
                    lock_key = f"hermes:lock:{run.id}"
                    ttl = await self.redis.ttl(lock_key)
                    if ttl == -2:
                        run.state = RunState.aborted_unsafe
                        run.finished_at = datetime.now(timezone.utc)
                        run.exit_code = -1
                        stale.append(run)
                    else:
                        live_run_ids.add(str(run.id))
                if stale:
                    await session.commit()
                    for run in stale:
                        logger.warning(
                            "orphan_recovered", run_id=str(run.id)
                        )
                else:
                    await session.rollback()
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
                run.started_at = datetime.now(timezone.utc)
                if run.kind == RunKind.task and run.task_id is not None:
                    task = await session.get(Task, run.task_id)
                    if task is not None:
                        task.status = TaskStatus.in_progress

                # Per-run lock — used by orphan reclaim to find stranded rows.
                lock_key = f"hermes:lock:{run.id}"
                await self.redis.set(lock_key, str(run.id), ex=max(_LOCK_TTL_RUNNING, lease_ttl), nx=True)

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
                    db_run.finished_at = datetime.now(timezone.utc)
                    db_run.transcript_object_key = completion_meta.get("transcript_object_key")
                    if completion_meta.get("token_input") is not None:
                        db_run.token_input = int(completion_meta["token_input"])
                    if completion_meta.get("token_output") is not None:
                        db_run.token_output = int(completion_meta["token_output"])
                    if completion_meta.get("cost_usd_micros") is not None:
                        db_run.cost_usd_micros = int(completion_meta["cost_usd_micros"])
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
                    headers={"X-Daedalus-Internal-Key": self.settings.session_secret},
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
        connector_spec = run.connector_snapshot or {}

        async for session in get_session():
            if run.task_id is not None:
                task = await session.get(Task, run.task_id)
            project = await session.get(Project, run.project_id)
            if not connector_spec and task is not None:
                connector_id = task.connector_id or getattr(project, "default_connector_id", None)
                if connector_id:
                    result = await session.execute(
                        select(Connector).where(Connector.connector_id == connector_id)
                    )
                    connector = result.scalar_one_or_none()
                    if connector is not None:
                        connector_spec = connector.spec
            break

        resource_limits = dict(connector_spec.get("resource_limits", {}))
        if project is not None and project.wall_clock_minutes_override is not None:
            resource_limits["wall_clock_minutes"] = project.wall_clock_minutes_override

        return {
            "run_id": str(run.id),
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "task_model": project.task_model if project else None,
                "verifier_model": project.verifier_model if project else None,
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
            if run.state == RunState.completed:
                project_for_argus = await session.get(Project, run.project_id)
                argus_on = project_for_argus is None or project_for_argus.argus_enabled
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
                task.status = TaskStatus.needs_fixes
            return

        if run.kind != RunKind.argus:
            return

        verify_exit_code = run.exit_code
        verify_output = ""
        if run.transcript_object_key:
            try:
                verify_output = get_object_store().get_text(run.transcript_object_key)
            except Exception:
                logger.warning("argus.transcript_fetch_failed", run_id=str(run.id))

        project = await session.get(Project, task.project_id)
        diff_text = ""
        if run.worktree_path and project is not None:
            try:
                diff_text = await collect_diff(run.worktree_path, project.git_default_branch)
            except Exception:
                logger.warning("argus.diff_collect_failed", run_id=str(run.id))

        connector_spec = run.connector_snapshot or {}
        verify_commands = connector_spec.get("verify_commands") or []

        argus_result = await argus_verify_run(
            task_title=task.title,
            task_description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            verify_commands=list(verify_commands),
            diff_text=diff_text,
            verify_output=verify_output,
            verify_exit_code=verify_exit_code,
            verifier_model=project.verifier_model if project else None,
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

        diff_hash = await self._compute_diff_hash(run, await session.get(Project, task.project_id))
        if diff_hash and task.last_diff_hash == diff_hash:
            logger.warning(
                "fix_loop_no_progress",
                task_id=str(task.id),
                diff_hash=diff_hash,
            )
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
        if project is not None and task.fix_loop_count > project.max_fix_loops:
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
