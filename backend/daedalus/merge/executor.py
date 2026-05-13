"""Executor — given a plan, materialize an integration worktree on a fresh
`daedalus-merge-<batch_id>` branch, sequentially merge the `clean` candidates,
run the connector's verify_commands against the result, and persist a
MergeBatch + MergeBatchItem row per branch.

Never touches main/master. The user reviews the integration branch and ships
manually (or via the ship endpoint).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import (
    OPEN_MERGE_BATCH_STATES,
    MergeBatch,
    MergeBatchItem,
    MergeBatchState,
    MergeItemCategory,
    MergeItemState,
)
from daedalus.merge.planner import BranchPlan

logger = structlog.get_logger()

ResultState = Literal[
    "merged",
    "skipped-conflict",
    "skipped-empty",
    "skipped-already-merged",
    "skipped-missing",
    "failed",
]


@dataclass
class MergeResult:
    plan: BranchPlan
    state: ResultState
    error: str | None = None


@dataclass
class BatchResult:
    batch_id: uuid.UUID
    integration_branch: str
    integration_worktree: str
    state: str
    results: list[MergeResult] = field(default_factory=list)
    verify_exit_code: int | None = None
    verify_output: str = ""
    verify_skipped: bool = False
    error: str | None = None

    @property
    def merged_count(self) -> int:
        return sum(1 for r in self.results if r.state == "merged")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.state.startswith("skipped"))

    @property
    def conflict_count(self) -> int:
        return sum(1 for r in self.results if r.state == "skipped-conflict")


class MergeBatchClaimConflict(Exception):
    """Raised when a batch can't be created because some of its candidate
    tasks are already claimed by another open batch (state in
    OPEN_MERGE_BATCH_STATES). Carries a mapping task_id -> batch_id so the
    caller can either surface or redirect to the existing batch."""

    def __init__(
        self,
        conflicts: dict[uuid.UUID, uuid.UUID],
        message: str | None = None,
    ) -> None:
        self.conflicts = conflicts
        if message is None:
            sample = ", ".join(
                f"task {tid} → batch {bid}" for tid, bid in list(conflicts.items())[:3]
            )
            extra = "" if len(conflicts) <= 3 else f" (+{len(conflicts) - 3} more)"
            message = (
                f"{len(conflicts)} task(s) already belong to an open merge batch: "
                f"{sample}{extra}. Ship or abort the existing batch before creating a new one."
            )
        super().__init__(message)


async def find_claim_conflicts(
    db: AsyncSession,
    project_id: uuid.UUID,
    candidate_task_ids: list[uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID]:
    """Return {task_id: open_batch_id} for any candidate task that is
    already an item of a non-terminal MergeBatch in the same project.

    "Open" means batch.state ∈ OPEN_MERGE_BATCH_STATES. Terminal states
    (shipped/failed/aborted) release the claim."""
    if not candidate_task_ids:
        return {}
    res = await db.execute(
        select(MergeBatchItem.task_id, MergeBatchItem.batch_id)
        .join(MergeBatch, MergeBatch.id == MergeBatchItem.batch_id)
        .where(
            MergeBatch.project_id == project_id,
            MergeBatch.state.in_(OPEN_MERGE_BATCH_STATES),
            MergeBatchItem.task_id.in_(candidate_task_ids),
        )
    )
    conflicts: dict[uuid.UUID, uuid.UUID] = {}
    for task_id, batch_id in res.all():
        if task_id is None:
            continue
        conflicts.setdefault(task_id, batch_id)
    return conflicts


_CATEGORY_TO_ITEM_STATE: dict[str, MergeItemState] = {
    "clean": MergeItemState.merged,
    "conflict": MergeItemState.skipped_conflict,
    "empty": MergeItemState.skipped_empty,
    "already-merged": MergeItemState.skipped_already_merged,
    "missing-branch": MergeItemState.skipped_missing,
    "missing-run": MergeItemState.skipped_missing,
}

_CATEGORY_TO_ENUM: dict[str, MergeItemCategory] = {
    "clean": MergeItemCategory.clean,
    "conflict": MergeItemCategory.conflict,
    "empty": MergeItemCategory.empty,
    "already-merged": MergeItemCategory.already_merged,
    "missing-branch": MergeItemCategory.missing_branch,
    "missing-run": MergeItemCategory.missing_run,
}


async def execute_batch(
    db: AsyncSession,
    project_id: uuid.UUID,
    workspace_path: str,
    default_branch: str,
    plans: list[BranchPlan],
    verify_commands: list[str],
    require_argus_pass: bool,
    created_by_user_id: uuid.UUID | None,
    agent_uid: int | None = None,
    agent_gid: int | None = None,
) -> BatchResult:
    """Materialize the integration branch and sequentially merge `clean` plans.
    Persists a MergeBatch + per-branch MergeBatchItem and returns a structured
    BatchResult mirroring the persisted state.

    Raises `MergeBatchClaimConflict` if any candidate task is already part
    of another open batch in the same project. The caller should surface
    the conflict (or redirect the user to the existing batch) rather than
    create a duplicate batch over the same task set."""
    candidate_task_ids = [p.candidate.task_id for p in plans if p.candidate.task_id is not None]
    conflicts = await find_claim_conflicts(db, project_id, candidate_task_ids)
    if conflicts:
        raise MergeBatchClaimConflict(conflicts)

    batch_id = uuid.uuid4()
    integration_branch = f"daedalus-merge-{batch_id}"
    worktree_dir = os.path.join(workspace_path, "runs", "merges", str(batch_id))

    batch = MergeBatch(
        id=batch_id,
        project_id=project_id,
        created_by_user_id=created_by_user_id,
        integration_branch=integration_branch,
        integration_worktree=worktree_dir,
        state=MergeBatchState.merging_clean,
        require_argus_pass=require_argus_pass,
    )
    db.add(batch)
    await db.flush()

    items: list[MergeBatchItem] = []
    for plan in plans:
        item = MergeBatchItem(
            id=uuid.uuid4(),
            batch_id=batch_id,
            task_id=plan.candidate.task_id,
            source_run_id=plan.candidate.run_id,
            branch=plan.candidate.branch or "",
            category=_CATEGORY_TO_ENUM.get(plan.category, MergeItemCategory.missing_run),
            state=MergeItemState.pending,
            conflicting_files=list(plan.conflicting_files),
            commits_ahead=plan.commits_ahead,
            files_changed=plan.files_changed,
        )
        db.add(item)
        items.append(item)
    await db.flush()

    result = BatchResult(
        batch_id=batch_id,
        integration_branch=integration_branch,
        integration_worktree=worktree_dir,
        state=MergeBatchState.merging_clean.value,
    )

    rc, _, _ = await _git(
        workspace_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{integration_branch}"
    )
    if rc == 0:
        result.error = f"integration branch {integration_branch} already exists"
        batch.error = result.error
        batch.state = MergeBatchState.failed
        return result

    os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)
    # The api container creates these dirs as root, but talos/argus-worker
    # and the host operator (uid 1000) will need to clean them up later.
    # Chown every fresh level we may have just created (runs/, runs/merges/)
    # to the agent uid before the worktree gets put in place. Walk-up bounded
    # at workspace_path so we never touch the workspace itself.
    if agent_uid is not None and agent_gid is not None:
        cursor = os.path.dirname(worktree_dir)
        ws_real = os.path.realpath(workspace_path)
        while cursor and os.path.realpath(cursor) != ws_real:
            try:
                st = os.stat(cursor)
            except OSError:
                break
            if st.st_uid != agent_uid or st.st_gid != agent_gid:
                try:
                    os.chown(cursor, agent_uid, agent_gid)
                except OSError as exc:
                    logger.warning(
                        "merge_chown_parent_failed", path=cursor, error=str(exc)
                    )
                    break
            parent = os.path.dirname(cursor)
            if parent == cursor:
                break
            cursor = parent
    rc, _, err = await _git(
        workspace_path, "worktree", "add", "-b", integration_branch, worktree_dir, default_branch
    )
    if rc != 0:
        result.error = f"worktree create failed: {err.strip()}"
        batch.error = result.error
        batch.state = MergeBatchState.failed
        return result
    if agent_uid is not None and agent_gid is not None:
        _chown_tree(worktree_dir, agent_uid, agent_gid)
        admin = os.path.join(workspace_path, ".git", "worktrees", str(batch_id))
        if os.path.isdir(admin):
            _chown_tree(admin, agent_uid, agent_gid)

    for plan, item in zip(plans, items):
        if plan.category != "clean":
            item.state = _CATEGORY_TO_ITEM_STATE.get(plan.category, MergeItemState.skipped_missing)
            result.results.append(MergeResult(plan=plan, state=_skip_state(plan.category)))
            continue
        rc, _, err = await _git(
            worktree_dir,
            "merge",
            "--no-ff",
            "-m",
            f"merge: {plan.candidate.task_title} ({plan.candidate.run_id})",
            plan.candidate.branch,
        )
        if rc == 0:
            item.state = MergeItemState.merged
            result.results.append(MergeResult(plan=plan, state="merged"))
            continue
        # Unexpected late conflict — earlier merges in this batch shifted
        # the integration tip. Abort cleanly and reclassify.
        await _git(worktree_dir, "merge", "--abort")
        item.state = MergeItemState.skipped_conflict
        item.category = MergeItemCategory.conflict
        item.error = err.strip()[:500]
        # Refresh conflicting_files via a fresh dry-run against the new tip.
        rc2, conflicts = await _merge_tree_conflicts(worktree_dir, "HEAD", plan.candidate.branch)
        if conflicts:
            item.conflicting_files = conflicts
        new_plan = BranchPlan(
            candidate=plan.candidate,
            category="conflict",
            conflicting_files=item.conflicting_files,
            commits_ahead=plan.commits_ahead,
            files_changed=plan.files_changed,
        )
        result.results.append(
            MergeResult(plan=new_plan, state="skipped-conflict", error=item.error)
        )

    if verify_commands and result.merged_count > 0:
        script = "set -e\n" + "\n".join(verify_commands)
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            script,
            cwd=worktree_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        result.verify_exit_code = proc.returncode if proc.returncode is not None else 1
        result.verify_output = out.decode(errors="replace")[-20_000:]
        batch.verify_exit_code = result.verify_exit_code
        batch.verify_output = result.verify_output
    else:
        result.verify_skipped = True

    if result.conflict_count > 0:
        batch.state = MergeBatchState.resolving
    else:
        batch.state = MergeBatchState.awaiting_review
    result.state = batch.state.value
    return result


def _skip_state(category: str) -> ResultState:
    if category == "conflict":
        return "skipped-conflict"
    if category == "empty":
        return "skipped-empty"
    if category == "already-merged":
        return "skipped-already-merged"
    return "skipped-missing"


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


async def _merge_tree_conflicts(cwd: str, base: str, branch: str) -> tuple[int, list[str]]:
    rc, out, _ = await _git(cwd, "merge-tree", "--write-tree", "--no-messages", base, branch)
    if rc == 0:
        return (0, [])
    files: set[str] = set()
    for line in out.splitlines()[1:]:
        if not line.strip():
            break
        if "\t" in line:
            files.add(line.split("\t", 1)[1].strip())
    return (rc, sorted(files))


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
        logger.warning("merge_chown_failed", path=path, error=str(exc))
