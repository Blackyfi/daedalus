"""HermesClient — Redis-backed single-runner queue with priority lanes,
DAG dependency tracking, job state machine, and lifecycle signals.

Imported by the API routes:  from daedalus.hermes.client import HermesClient
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.core.settings import get_settings
from daedalus.db.models import (
    Connector,
    PriorityLane,
    Project,
    Run,
    RunKind,
    RunState,
    Snapshot,
    Task,
    TaskStatus,
)
from daedalus.db.redis import get_redis

logger = structlog.get_logger()

# ── Redis keys ──────────────────────────────────────────────────────────────

_QUEUE_PREFIX = "hermes:queue"
_DEPS_PREFIX = "hermes:deps"
_SIGNAL_PREFIX = "hermes:signal"
_PTY_PREFIX = "pty"

EVENT_PROJECT_PREFIX = "events:project"
EVENT_QUEUE_CHANNEL = "events:queue"

_LANE_ORDER = (PriorityLane.urgent, PriorityLane.default, PriorityLane.bg)


@dataclass
class _EnqueuePayload:
    """Serializable payload stored in a Redis LIST entry."""

    run_id: str
    kind: str
    task_id: str | None
    project_id: str
    lane: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> bytes:
        return json.dumps({
            "run_id": self.run_id,
            "kind": self.kind,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "lane": self.lane,
            "extra": self.extra,
        }).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> _EnqueuePayload:
        d = json.loads(data)
        return cls(
            run_id=d["run_id"],
            kind=d["kind"],
            task_id=d.get("task_id"),
            project_id=d["project_id"],
            lane=d["lane"],
            extra=d.get("extra", {}),
        )


# ── Client ──────────────────────────────────────────────────────────────────


class HermesClient:
    """Facade for enqueueing runs, tracking DAG dependencies, and sending
    lifecycle signals to a running job.

    Parameters
    ----------
    db : AsyncSession
        The current SQLAlchemy async session (caller does commit).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.redis = get_redis()
        self.settings = get_settings()

    # ── event fan-out ─────────────────────────────────────────────────────

    async def publish_project_event(self, project_id: uuid.UUID | str, payload: dict[str, Any]) -> None:
        await self.redis.publish(
            f"{EVENT_PROJECT_PREFIX}:{project_id}",
            json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **payload}),
        )

    async def publish_queue_event(self, payload: dict[str, Any]) -> None:
        lane_lengths = {
            lane.value: int(await self.redis.llen(f"{_QUEUE_PREFIX}:{lane.value}"))
            for lane in _LANE_ORDER
        }
        await self.redis.publish(
            EVENT_QUEUE_CHANNEL,
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "lane_lengths": lane_lengths,
                    **payload,
                }
            ),
        )

    # ── public enqueue API ────────────────────────────────────────────────

    async def enqueue_task(self, task: Task) -> Run:
        """Enqueue a task run at default priority."""
        task.status = TaskStatus.ready
        return await self._create_run(
            run=Run(
                task_id=task.id,
                project_id=task.project_id,
                kind=RunKind.task,
                lane=PriorityLane.default,
            ),
        )

    async def enqueue_planning(self, project: Project) -> Run:
        """Enqueue a planning/generate-tasks run."""
        return await self._create_run(
            run=Run(
                project_id=project.id,
                kind=RunKind.planning,
                lane=PriorityLane.urgent,
            ),
        )

    async def enqueue_argus_verify(self, run: Run, task: Task) -> Run:
        """Enqueue an argus verification run for the given task."""
        task.status = TaskStatus.verifying
        return await self._create_run(
            run=Run(
                task_id=task.id,
                project_id=task.project_id,
                kind=RunKind.argus,
                lane=PriorityLane.default,
                connector_snapshot=run.connector_snapshot,
                worktree_path=run.worktree_path,
            ),
        )

    async def enqueue_fix_task(
        self, task: Task, findings: list[dict[str, Any]]
    ) -> Run:
        """Enqueue a fix task run carrying the findings that triggered it."""
        return await self._create_run(
            run=Run(
                task_id=task.id,
                project_id=task.project_id,
                kind=RunKind.task,
                lane=PriorityLane.default,
                connector_snapshot={"findings": findings, "reason": "fix_loop"},
            ),
        )

    # ── internal helpers ──────────────────────────────────────────────────

    async def _create_run(self, run: Run) -> Run:
        """Persist the Run, record DAG dependencies, create worktree, and
        append to the Redis queue list.  Returns the persisted Run."""

        if not run.connector_snapshot:
            run.connector_snapshot = await self._connector_snapshot_for_run(run)

        self.db.add(run)
        await self.db.flush()  # assign run.id

        # DAG: for each dependency task, record that this run_id depends on it.
        # Redis key: hermes:deps:{dep_task_id} -> SET of run_ids
        if run.task_id is not None:
            task_res = await self.db.execute(
                select(Task).where(Task.id == run.task_id)
            )
            task = task_res.scalar_one_or_none()
            if task and task.depends_on:
                for dep_id in task.depends_on:
                    deps_key = f"{_DEPS_PREFIX}:{dep_id}"
                    await self.redis.sadd(deps_key, str(run.id))
                logger.info(
                    "record_deps",
                    run_id=str(run.id),
                    depends=list(task.depends_on),
                )

        # Create worktree for the run.
        await self._ensure_worktree(run)

        # Pre-yolo snapshot — captured per §10.6 / §14.3 so destructive runs
        # can be rolled back in one click.
        await self._maybe_create_yolo_snapshot(run)

        # Enqueue on Redis LIST (right-end = latest job).
        payload = _EnqueuePayload(
            run_id=str(run.id),
            kind=run.kind.value,
            task_id=str(run.task_id) if run.task_id else None,
            project_id=str(run.project_id),
            lane=run.lane.value,
        )
        queue_key = f"{_QUEUE_PREFIX}:{run.lane.value}"
        await self.redis.rpush(queue_key, payload.to_json())

        logger.info(
            "enqueued",
            run_id=str(run.id),
            kind=run.kind.value,
            lane=run.lane.value,
        )

        await self.publish_project_event(
            run.project_id,
            {
                "kind": "run.enqueued",
                "run_id": str(run.id),
                "run_kind": run.kind.value,
                "task_id": str(run.task_id) if run.task_id else None,
            },
        )
        await self.publish_queue_event(
            {"kind": "enqueued", "run_id": str(run.id), "lane": run.lane.value}
        )

        return run

    async def _ensure_worktree(self, run: Run) -> None:
        """Create (or verify) a git worktree at
        {project.workspace_path}/runs/{run_id}.
        """
        # Only `task` runs actually operate on a working tree; planning
        # talks to the LLM about the project's metadata, cleanup is
        # bookkeeping, and argus reuses the parent task run's worktree
        # (set explicitly via `worktree_path=run.worktree_path`).
        if run.kind not in (RunKind.task,) and run.worktree_path is None:
            return

        res = await self.db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = res.scalar_one_or_none()
        if project is None:
            return

        worktree_path = os.path.join(
            project.workspace_path,
            "runs",
            str(run.id),
        )

        # Skip if the worktree_path column is already set (already created).
        if run.worktree_path is not None:
            return

        os.makedirs(worktree_path, exist_ok=True)

        # Create a fresh branch off the project's default branch and
        # check that out into the worktree. Without `-b` git would try
        # to check out the default branch directly, which fails with
        # "fatal: 'main' is already used by worktree at <root>" because
        # the root checkout already has it.
        new_branch = f"daedalus-run-{run.id}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add",
                "-b", new_branch,
                worktree_path,
                project.git_default_branch,
                cwd=project.workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode not in (0, None):
                logger.warning(
                    "worktree_create_failed",
                    run_id=str(run.id),
                    worktree_path=worktree_path,
                    error=stderr.decode().strip(),
                )
                return
        except Exception as exc:
            logger.warning(
                "worktree_create_failed",
                run_id=str(run.id),
                worktree_path=worktree_path,
                error=str(exc),
            )

        # The api/hermes containers run as root, but talos/argus-worker run
        # as the daedalus user (uid 1000) so claude can use
        # `--dangerously-skip-permissions`. Without this chown the agent
        # finds its own worktree read-only, falls back to side-cloning into
        # /home/daedalus, and verifier sees an empty diff.
        # Two locations need ownership: the worktree itself, and the
        # corresponding `.git/worktrees/<run_id>/` admin dir where git
        # writes HEAD on each commit.
        agent_uid = self.settings.agent_uid
        agent_gid = self.settings.agent_gid
        if agent_uid is not None and agent_gid is not None:
            self._chown_tree(worktree_path, agent_uid, agent_gid)
            admin_dir = os.path.join(
                project.workspace_path, ".git", "worktrees", str(run.id)
            )
            if os.path.isdir(admin_dir):
                self._chown_tree(admin_dir, agent_uid, agent_gid)

        self._ensure_artifact_gitignore(worktree_path)

        run.worktree_path = worktree_path

    @staticmethod
    def _ensure_artifact_gitignore(worktree_path: str) -> None:
        """Append standard compiled-artefact patterns to the worktree's
        .gitignore if missing. Prevents agents from committing
        .pyc/__pycache__/node_modules/etc. when a project's own .gitignore
        doesn't already cover them — the agent keeps full freedom to create
        those files at runtime; they just can't end up in the diff fed to
        Argus. See task 5256b444 in the needs_fixes audit (commit contained
        only .pyc files)."""
        patterns = [
            "__pycache__/",
            "*.py[cod]",
            "*$py.class",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
            "node_modules/",
            "dist/",
            "build/",
            ".next/",
            ".nuxt/",
            "target/",
            ".gradle/",
            ".idea/",
            ".vscode/",
            "*.class",
            "*.o",
            "*.so",
            "*.dylib",
        ]
        gitignore_path = os.path.join(worktree_path, ".gitignore")
        try:
            existing = ""
            if os.path.exists(gitignore_path):
                with open(gitignore_path, encoding="utf-8", errors="replace") as fh:
                    existing = fh.read()
            existing_lines = {line.strip() for line in existing.splitlines()}
            missing = [p for p in patterns if p not in existing_lines]
            if not missing:
                return
            header = "\n# Daedalus: auto-appended compiled-artefact patterns\n"
            with open(gitignore_path, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(header)
                fh.write("\n".join(missing) + "\n")
        except Exception as exc:
            logger.warning(
                "gitignore_patch_failed", path=worktree_path, error=str(exc)
            )

    @staticmethod
    def _chown_tree(path: str, uid: int, gid: int) -> None:
        try:
            os.chown(path, uid, gid)
            for root, dirs, files in os.walk(path):
                for name in dirs:
                    try:
                        os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)
                    except OSError:
                        pass
                for name in files:
                    try:
                        os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)
                    except OSError:
                        pass
        except OSError as exc:
            logger.warning("worktree_chown_failed", path=path, error=str(exc))

    async def _maybe_create_yolo_snapshot(self, run: Run) -> None:
        """If the run's connector profile is `yolo`, tag the worktree HEAD
        as `daedalus-snap/<run_id>` and record a Snapshot row."""
        spec = run.connector_snapshot or {}
        if spec.get("permission_profile") != "yolo":
            return
        if not run.worktree_path:
            return

        tag = f"daedalus-snap/{run.id}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "tag", "-f", tag, "HEAD",
                cwd=run.worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode not in (0, None):
                logger.warning(
                    "snapshot_tag_failed",
                    run_id=str(run.id),
                    error=stderr.decode().strip(),
                )
                return
        except Exception:
            logger.exception("snapshot_tag_exc", run_id=str(run.id))
            return

        snap = Snapshot(
            project_id=run.project_id,
            run_id=run.id,
            git_tag=tag,
            note="pre-yolo snapshot",
        )
        self.db.add(snap)
        await self.db.flush()
        logger.info("snapshot_created", run_id=str(run.id), tag=tag)

    # ── DAG dependency check ──────────────────────────────────────────────

    async def run_dependencies_met(self, run: Run) -> bool:
        """Return True if all tasks listed in the run's task.depends_on
        have at least one completed/finished run."""
        if run.task_id is None:
            return True

        task_res = await self.db.execute(
            select(Task).where(Task.id == run.task_id)
        )
        task = task_res.scalar_one_or_none()
        if not task or not task.depends_on:
            return True

        done_states = (
            RunState.completed,
            RunState.cancelled,
            RunState.failed,
            RunState.aborted_unsafe,
        )
        res = await self.db.execute(
            select(Run.task_id).where(
                Run.task_id.in_(task.depends_on),
                Run.state.in_(done_states),
            )
        )
        done_task_ids = {row[0] for row in res.all() if row[0] is not None}
        return all(d in done_task_ids for d in task.depends_on)

    async def run_dependencies_met_from_deps_key(self, run: Run) -> bool:
        """Check Redis-set dependencies for this run."""
        deps_key = f"{_DEPS_PREFIX}:{run.task_id}"
        dep_run_ids = await self.redis.smembers(deps_key)
        if not dep_run_ids:
            return True

        done_states = {
            RunState.completed,
            RunState.cancelled,
            RunState.failed,
            RunState.aborted_unsafe,
        }
        res = await self.db.execute(
            select(Run.state).where(
                Run.id.in_(dep_run_ids)
            )
        )
        states = {row[0] for row in res.all()}
        return all(s in done_states for s in states)

    # ── lifecycle signals ─────────────────────────────────────────────────

    async def send_signal(
        self, run: Run, action: str
    ) -> None:
        """Send a lifecycle signal to Talos for *run*.

        Supported actions: pause, resume, interrupt, kill, detach.
        """
        signal_key = f"{_SIGNAL_PREFIX}:{run.id}"
        payload = {
            "run_id": str(run.id),
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.publish(signal_key, json.dumps(payload))
        logger.info("signal_sent", run_id=str(run.id), action=action)

    async def inject_text(self, run: Run, text: str) -> None:
        """Send interactive text to the running PTY."""
        signal_key = f"{_SIGNAL_PREFIX}:{run.id}"
        payload = {
            "run_id": str(run.id),
            "action": "inject",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.publish(signal_key, json.dumps(payload))
        logger.info("text_injected", run_id=str(run.id), len=len(text))

    async def resize_pty(self, run: Run, *, rows: int, cols: int) -> None:
        """Tell Talos to resize the PTY for *run*."""
        signal_key = f"{_SIGNAL_PREFIX}:{run.id}"
        payload = {
            "run_id": str(run.id),
            "action": "resize",
            "rows": rows,
            "cols": cols,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.publish(signal_key, json.dumps(payload))
        logger.info("pty_resized", run_id=str(run.id), rows=rows, cols=cols)

    # ── completion helpers (called by scheduler) ──────────────────────────

    async def complete_run(
        self,
        run: Run,
        *,
        exit_code: int | None = None,
        state: RunState | None = None,
        **extra: Any,
    ) -> None:
        """Finalise a run row in the database."""
        now = datetime.now(timezone.utc)
        run.state = state or RunState.completed
        run.exit_code = exit_code
        run.finished_at = now

        for k, v in extra.items():
            setattr(run, k, v)

        await self.db.commit()

    # ── dependency advancement ────────────────────────────────────────────

    async def advance_dependents(self, run: Run) -> int:
        """After *run* completes, find and enqueue any runs that were
        waiting on this run's task_id."""
        if run.task_id is None:
            return 0

        # Find all runs that depend on this task_id.
        deps_key = f"{_DEPS_PREFIX}:{run.task_id}"
        dependent_run_ids = await self.redis.smembers(deps_key)

        advanced = 0
        for rid in dependent_run_ids:
            try:
                rid_uuid = uuid.UUID(rid)
                await self._maybe_enqueue_dependent(rid_uuid)
                advanced += 1
            except Exception:
                logger.warning(
                    "advance_failed",
                    dependent_run_id=rid,
                    exc_info=True,
                )

        return advanced

    async def _maybe_enqueue_dependent(self, dependent_run_id: uuid.UUID) -> None:
        """If the dependent run's dependencies are now all met, enqueue it."""
        res = await self.db.execute(
            select(Run).where(Run.id == dependent_run_id)
        )
        dep_run = res.scalar_one_or_none()
        if dep_run is None:
            return

        met = await self.run_dependencies_met(dep_run)
        if not met:
            return

        # The dependent run was already enqueued by _create_run when it
        # was originally created; just ensure it is in a re-checkable state.
        logger.info(
            "dependent_ready",
            dependent_run_id=str(dep_run.id),
            depended_on_task_id=str(dep_run.task_id),
        )

    async def _connector_snapshot_for_run(self, run: Run) -> dict[str, Any]:
        if run.task_id is None:
            return {}

        task = await self.db.get(Task, run.task_id)
        if task is None:
            return {}

        project = await self.db.get(Project, run.project_id)
        connector_id = task.connector_id or getattr(project, "default_connector_id", None)
        if not connector_id:
            return {}

        res = await self.db.execute(select(Connector).where(Connector.connector_id == connector_id))
        connector = res.scalar_one_or_none()
        if connector is None:
            return {}
        if not connector.enabled:
            raise ValueError(f"connector {connector_id!r} is disabled")
        return connector.spec
