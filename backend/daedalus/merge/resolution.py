"""Agent-driven conflict resolution for a MergeBatch.

For every item in the batch with state=skipped_conflict, this module:

  1. Runs `git merge --no-commit --no-ff <branch>` in the integration worktree
     to introduce the conflict markers (or commits a clean fast merge if the
     conflict has resolved itself due to earlier merges).
  2. Creates a Daedalus Task: title="Resolve merge: <branch>", profile=yolo,
     connector=project default, with a description that names the conflicted
     files and explicit instructions (must end with a clean commit, never
     `git merge --abort`).
  3. Creates a queued Run pointing at the integration worktree (worktree_path
     pre-set so HermesClient._ensure_worktree short-circuits and Talos uses
     the existing path via active_worktree_path in _build_run_signal_payload).
  4. Updates the MergeBatchItem with resolution_task_id / resolution_run_id.

After the run completes, the scheduler's normal post-processing fires; the
verify_commands defined here check `git status --porcelain` is empty and HEAD
is a merge commit. If Argus passes, the item state is later flipped to
`resolved` by the resolution-watcher endpoint.

This is sequential by construction: only the FIRST pending conflict is
processed per call. After that one resolves, call again to advance to the
next. The frontend polls and re-invokes until done.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import (
    Connector,
    MergeBatch,
    MergeBatchItem,
    MergeBatchState,
    MergeItemState,
    PriorityLane,
    Project,
    Run,
    RunKind,
    RunState,
    Task,
    TaskPriority,
    TaskStatus,
)
from daedalus.hermes.client import HermesClient

logger = structlog.get_logger()


@dataclass
class ResolutionStep:
    item_id: uuid.UUID
    branch: str
    state: str  # "queued" | "auto-merged" | "no-action" | "failed"
    task_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    error: str | None = None


_VERIFY_COMMANDS = [
    # No leftover conflict markers in tracked files.
    "! git diff --check",
    # Clean working tree (no unstaged/uncommitted changes).
    "test -z \"$(git status --porcelain)\"",
    # HEAD is a merge commit — i.e., the merge was actually completed.
    "git rev-parse --verify HEAD^2 >/dev/null",
]


def _build_resolution_description(branch: str, files: list[str]) -> str:
    file_list = "\n".join(f"  - {f}" for f in files) if files else "  (no files reported)"
    return f"""\
**Resolve merge conflicts on the integration branch.**

The integration branch already has `{branch}` in a partially-merged state:
`git merge --no-commit --no-ff {branch}` was run before this task started, so
your working tree contains live conflict markers.

Conflicted files reported by git:
{file_list}

What you must do:

1. Inspect each conflicted file with `git status` and read the conflict
   markers (`<<<<<<<`, `=======`, `>>>>>>>`).
2. For each conflict, decide which side is correct, or merge them carefully.
   Read the file history (`git log -p --follow <path>`) on each branch if
   the right resolution isn't obvious.
3. Remove all conflict markers, save the resolved files, then `git add` each
   resolved path.
4. Run `git status` to confirm there are no remaining unmerged paths.
5. Complete the merge with a single `git commit` — the default merge message
   is fine, or improve it. **Do NOT run `git merge --abort`** — that defeats
   the entire point of this task.
6. After committing, run `git diff --check` and `git status --porcelain` to
   prove the tree is clean. Print the result.

The verify pipeline will check three invariants:
  - `git diff --check` returns empty (no leftover markers)
  - `git status --porcelain` returns empty (clean tree)
  - `git rev-parse HEAD^2` succeeds (HEAD really is a merge commit)
"""


async def resolve_next_conflict(
    db: AsyncSession,
    batch_id: uuid.UUID,
) -> ResolutionStep | None:
    """Pick the first `skipped_conflict` item with no resolution task yet,
    introduce the conflict in the integration worktree, and queue a resolver
    task targeting that worktree. Returns None if nothing to do."""
    batch = await db.get(MergeBatch, batch_id)
    if batch is None:
        return None

    items_res = await db.execute(
        select(MergeBatchItem)
        .where(
            MergeBatchItem.batch_id == batch_id,
            MergeBatchItem.state == MergeItemState.skipped_conflict,
            MergeBatchItem.resolution_task_id.is_(None),
        )
        .order_by(MergeBatchItem.created_at.asc())
        .limit(1)
    )
    item = items_res.scalar_one_or_none()
    if item is None:
        return None

    project = await db.get(Project, batch.project_id)
    if project is None:
        return ResolutionStep(item_id=item.id, branch=item.branch, state="failed", error="project gone")

    worktree = batch.integration_worktree
    if not os.path.isdir(worktree):
        item.state = MergeItemState.resolution_failed
        item.error = f"integration worktree missing: {worktree}"
        return ResolutionStep(item_id=item.id, branch=item.branch, state="failed", error=item.error)

    # Reset any in-progress merge state from a prior aborted attempt.
    await _git(worktree, "merge", "--abort")
    rc, _, _err = await _git(worktree, "merge", "--no-commit", "--no-ff", item.branch)
    if rc == 0:
        # No conflict this time around — earlier merges resolved it. Commit
        # the auto-merge and mark resolved without spawning an agent.
        rc2, _, _err2 = await _git(
            worktree,
            "commit",
            "--no-edit",
            "-m",
            f"merge: {item.branch} (auto-merged after earlier conflicts cleared)",
        )
        if rc2 != 0:
            # Could happen if there's nothing to commit (already up to date).
            await _git(worktree, "merge", "--abort")
            item.state = MergeItemState.resolved
            item.error = None
            return ResolutionStep(
                item_id=item.id, branch=item.branch, state="no-action"
            )
        item.state = MergeItemState.resolved
        item.error = None
        return ResolutionStep(item_id=item.id, branch=item.branch, state="auto-merged")

    # Real conflict — leave the markers in place and spawn the agent.
    connector_spec, connector_id = await _resolve_connector(db, project)
    if not connector_spec:
        item.state = MergeItemState.resolution_failed
        item.error = "no default connector configured for project"
        await _git(worktree, "merge", "--abort")
        return ResolutionStep(item_id=item.id, branch=item.branch, state="failed", error=item.error)

    # Override verify_commands so the agent is judged on merge cleanliness,
    # not on whatever the project usually checks.
    spec_for_run = dict(connector_spec)
    spec_for_run["verify_commands"] = list(_VERIFY_COMMANDS)

    description = _build_resolution_description(item.branch, list(item.conflicting_files))

    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        title=f"Resolve merge: {item.branch}",
        description=description,
        acceptance_criteria=(
            "All conflict markers removed; `git status --porcelain` empty; "
            "HEAD is a merge commit (`git rev-parse HEAD^2` succeeds)."
        ),
        status=TaskStatus.ready,
        priority=TaskPriority.P1,
        connector_id=connector_id,
        profile="yolo",
        tags=["merge-resolve", f"batch:{batch_id}"],
    )
    db.add(task)
    await db.flush()

    run = Run(
        project_id=project.id,
        task_id=task.id,
        kind=RunKind.task,
        state=RunState.queued,
        lane=PriorityLane.default,
        connector_snapshot=spec_for_run,
        worktree_path=worktree,  # pre-set: short-circuits _ensure_worktree
    )
    # Hand off to Hermes — _create_run flushes, skips worktree creation
    # (because run.worktree_path is already set), and pushes to Redis.
    client = HermesClient(db)
    await client._create_run(run)

    item.state = MergeItemState.resolution_queued
    item.resolution_task_id = task.id
    item.resolution_run_id = run.id
    item.error = None
    return ResolutionStep(
        item_id=item.id,
        branch=item.branch,
        state="queued",
        task_id=task.id,
        run_id=run.id,
    )


async def reconcile_resolution_states(
    db: AsyncSession, batch_id: uuid.UUID
) -> list[uuid.UUID]:
    """Reflect resolution-run completion onto the items. Called from the
    polling endpoint. Returns ids whose state changed."""
    batch = await db.get(MergeBatch, batch_id)
    if batch is None:
        return []
    items_res = await db.execute(
        select(MergeBatchItem).where(
            MergeBatchItem.batch_id == batch_id,
            MergeBatchItem.resolution_run_id.isnot(None),
            MergeBatchItem.state.in_(
                [MergeItemState.resolution_queued, MergeItemState.resolution_running]
            ),
        )
    )
    changed: list[uuid.UUID] = []
    for item in items_res.scalars():
        run = await db.get(Run, item.resolution_run_id)
        if run is None:
            continue
        if run.state in (RunState.queued,):
            continue
        if run.state in (RunState.claimed, RunState.running):
            if item.state != MergeItemState.resolution_running:
                item.state = MergeItemState.resolution_running
                changed.append(item.id)
            continue
        # Terminal state.
        # Re-check the worktree to see if the merge actually landed: HEAD
        # must be a merge commit AND the working tree must be clean.
        worktree = batch.integration_worktree
        if await _is_clean_merge(worktree):
            item.state = MergeItemState.resolved
            item.error = None
        else:
            item.state = MergeItemState.resolution_failed
            item.error = (
                f"resolution run finished in state={run.state.value} but the "
                "integration worktree is not a clean merge commit"
            )
        changed.append(item.id)

    # If every item is either merged/resolved or skipped-non-conflict, advance
    # the batch.
    items_all = (
        await db.execute(select(MergeBatchItem).where(MergeBatchItem.batch_id == batch_id))
    ).scalars().all()
    pending_conflicts = [
        i for i in items_all
        if i.state in (
            MergeItemState.skipped_conflict,
            MergeItemState.resolution_queued,
            MergeItemState.resolution_running,
        )
    ]
    if not pending_conflicts and any(
        i.state == MergeItemState.resolution_failed for i in items_all
    ):
        batch.state = MergeBatchState.failed
    elif not pending_conflicts:
        batch.state = MergeBatchState.awaiting_review
    return changed


async def _resolve_connector(
    db: AsyncSession, project: Project
) -> tuple[dict, str | None]:
    if not project.default_connector_id:
        return ({}, None)
    res = await db.execute(
        select(Connector).where(Connector.connector_id == project.default_connector_id)
    )
    connector = res.scalar_one_or_none()
    if connector is None:
        return ({}, None)
    return (dict(connector.spec or {}), connector.connector_id)


async def _is_clean_merge(worktree: str) -> bool:
    rc, out, _ = await _git(worktree, "status", "--porcelain")
    if rc != 0 or out.strip():
        return False
    rc, _, _ = await _git(worktree, "rev-parse", "--verify", "HEAD^2")
    return rc == 0


async def _git(cwd: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )
