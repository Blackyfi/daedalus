"""Ship a merge batch onto the project's default branch.

Fast-forward only: if the default branch has moved since the integration
branch was cut, refuse and tell the operator. Optionally prune the merged
source branches and remove the integration worktree on success.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import (
    OPEN_MERGE_BATCH_STATES,
    MergeBatch,
    MergeBatchItem,
    MergeBatchState,
    MergeItemState,
    Project,
    Run,
)

logger = structlog.get_logger()


@dataclass
class ShipResult:
    state: str  # "shipped" | "failed"
    integration_branch: str
    default_branch: str
    pruned_branches: list[str]
    removed_worktree: bool
    error: str | None = None
    # Sibling batches auto-aborted because their work is now reachable
    # from `default_branch` via the just-shipped batch. Populated by
    # `_reconcile_superseded_batches` after a successful ship.
    superseded_batch_ids: list[uuid.UUID] = field(default_factory=list)


async def ship_batch(
    db: AsyncSession,
    batch_id: uuid.UUID,
    delete_source_branches: bool = True,
    remove_worktree: bool = True,
) -> ShipResult:
    batch = await db.get(MergeBatch, batch_id)
    if batch is None:
        return ShipResult(
            state="failed",
            integration_branch="",
            default_branch="",
            pruned_branches=[],
            removed_worktree=False,
            error="batch not found",
        )
    project = await db.get(Project, batch.project_id)
    if project is None:
        return ShipResult(
            state="failed",
            integration_branch=batch.integration_branch,
            default_branch="",
            pruned_branches=[],
            removed_worktree=False,
            error="project gone",
        )

    if batch.state not in (MergeBatchState.awaiting_review,):
        return ShipResult(
            state="failed",
            integration_branch=batch.integration_branch,
            default_branch=project.git_default_branch,
            pruned_branches=[],
            removed_worktree=False,
            error=f"batch is not awaiting_review (state={batch.state.value})",
        )

    batch.state = MergeBatchState.shipping

    workspace = project.workspace_path
    default_branch = project.git_default_branch

    # Check FF-feasibility before we touch anything: integration must
    # contain default as an ancestor. Otherwise main has moved underneath us.
    rc, _, _ = await _git(
        workspace, "merge-base", "--is-ancestor", default_branch, batch.integration_branch
    )
    if rc != 0:
        batch.state = MergeBatchState.awaiting_review
        return ShipResult(
            state="failed",
            integration_branch=batch.integration_branch,
            default_branch=default_branch,
            pruned_branches=[],
            removed_worktree=False,
            error=(
                f"fast-forward refused: {default_branch} has moved past the "
                f"integration branch's base. Pull/rebase and re-run the batch."
            ),
        )

    # Update default branch by pointing its ref at the integration tip.
    # This avoids touching whatever worktree is checked out on default.
    rc, integ_oid, err = await _git(workspace, "rev-parse", batch.integration_branch)
    if rc != 0 or not integ_oid.strip():
        batch.state = MergeBatchState.awaiting_review
        return ShipResult(
            state="failed",
            integration_branch=batch.integration_branch,
            default_branch=default_branch,
            pruned_branches=[],
            removed_worktree=False,
            error=f"could not resolve integration tip: {err.strip()}",
        )
    rc, _, err = await _git(
        workspace,
        "update-ref",
        f"refs/heads/{default_branch}",
        integ_oid.strip(),
    )
    if rc != 0:
        batch.state = MergeBatchState.awaiting_review
        return ShipResult(
            state="failed",
            integration_branch=batch.integration_branch,
            default_branch=default_branch,
            pruned_branches=[],
            removed_worktree=False,
            error=f"update-ref failed: {err.strip()}",
        )

    # Best-effort: prune merged source branches and tear down the worktree.
    pruned: list[str] = []
    if delete_source_branches:
        items_res = await db.execute(
            select(MergeBatchItem).where(
                MergeBatchItem.batch_id == batch_id,
                MergeBatchItem.state.in_([MergeItemState.merged, MergeItemState.resolved]),
            )
        )
        for item in items_res.scalars():
            # Tear down the source-run worktree FIRST. `git branch -D`
            # refuses to delete a branch that still has a worktree checked
            # out, and even when it doesn't refuse the dangling worktree
            # admin entry would just bloat .git/worktrees/. Pulling the
            # path from the source Run row keeps us correct even if the
            # branch name doesn't follow the daedalus-run-<rid> convention.
            if item.source_run_id is not None:
                src_run = await db.get(Run, item.source_run_id)
                if src_run is not None and src_run.worktree_path:
                    rc, _, err = await _git(
                        workspace, "worktree", "remove", "--force", src_run.worktree_path
                    )
                    if rc != 0 and os.path.isdir(src_run.worktree_path):
                        # Worktree's git admin link is stale — try a manual
                        # rmtree so we at least reclaim the disk space.
                        try:
                            shutil.rmtree(src_run.worktree_path)
                        except OSError as exc:
                            logger.warning(
                                "ship_source_worktree_rmtree_failed",
                                path=src_run.worktree_path,
                                error=str(exc),
                            )
            rc, _, _ = await _git(workspace, "branch", "-D", item.branch)
            if rc == 0:
                pruned.append(item.branch)

    removed = False
    if remove_worktree:
        rc, _, _ = await _git(
            workspace, "worktree", "remove", "--force", batch.integration_worktree
        )
        if rc == 0:
            removed = True
        else:
            # Worktree dir may not exist anymore; just rmtree leftovers.
            if os.path.isdir(batch.integration_worktree):
                try:
                    shutil.rmtree(batch.integration_worktree)
                    removed = True
                except OSError:
                    pass
        # And drop the integration branch ref now that default points at it.
        await _git(workspace, "branch", "-D", batch.integration_branch)

    batch.state = MergeBatchState.shipped
    batch.shipped_at = datetime.now(timezone.utc)

    # Reconcile sibling batches: any other open batch in this project whose
    # work is now reachable from `default_branch` (i.e. its items either
    # landed via this ship, were already on main, or contribute nothing) is
    # superseded — its integration branch would no-op or fast-forward-refuse
    # on a manual ship. Auto-abort with a clear error so the UI does not
    # show "N merges ready to ship" pointing at zombies.
    superseded = await _reconcile_superseded_batches(
        db,
        project_id=batch.project_id,
        shipped_batch_id=batch.id,
        workspace=workspace,
        default_branch=default_branch,
    )

    return ShipResult(
        state="shipped",
        integration_branch=batch.integration_branch,
        default_branch=default_branch,
        pruned_branches=pruned,
        removed_worktree=removed,
        superseded_batch_ids=superseded,
    )


async def _reconcile_superseded_batches(
    db: AsyncSession,
    project_id: uuid.UUID,
    shipped_batch_id: uuid.UUID,
    workspace: str,
    default_branch: str,
) -> list[uuid.UUID]:
    """Find other open batches in this project whose item branches are
    all reachable from `default_branch` now, and mark them aborted.

    "Reachable" here means: for every item in the sibling batch, either
      (a) the source branch no longer exists locally (already cleaned up
          by some prior ship), or
      (b) the source branch's tip is an ancestor of `default_branch` (the
          work is in main), or
      (c) the source branch is empty / missing-run (contributes nothing).

    A batch satisfying that for *all* its items contributes no new
    commits to main; trying to ship it would no-op or fast-forward-refuse.
    Mark it aborted and record which batch superseded it."""
    res = await db.execute(
        select(MergeBatch).where(
            MergeBatch.project_id == project_id,
            MergeBatch.id != shipped_batch_id,
            MergeBatch.state.in_(OPEN_MERGE_BATCH_STATES),
        )
    )
    superseded: list[uuid.UUID] = []
    for other in res.scalars():
        items_res = await db.execute(
            select(MergeBatchItem).where(MergeBatchItem.batch_id == other.id)
        )
        items = list(items_res.scalars())
        if not items:
            continue
        if not await _all_items_in_main(workspace, default_branch, items):
            continue
        other.state = MergeBatchState.aborted
        prior = (other.error + "\n") if other.error else ""
        other.error = (
            f"{prior}Superseded by batch {shipped_batch_id} which shipped the "
            f"same task set. All source branches in this batch are now reachable "
            f"from {default_branch}; a manual ship would be a no-op or refused."
        )
        superseded.append(other.id)
        logger.info(
            "ship_superseded_batch_aborted",
            superseded_batch_id=str(other.id),
            by_batch_id=str(shipped_batch_id),
            project_id=str(project_id),
        )
    return superseded


async def _all_items_in_main(
    workspace: str, default_branch: str, items: list[MergeBatchItem]
) -> bool:
    """True iff every item in this batch contributes nothing main doesn't
    already have — its source branch is gone, missing, empty, or an
    ancestor of `default_branch`."""
    for item in items:
        if not item.branch:
            # Missing-run items never had a branch — they can't possibly
            # contribute work, so they don't block the supersession.
            continue
        rc, _, _ = await _git(
            workspace, "rev-parse", "--verify", "--quiet", f"refs/heads/{item.branch}"
        )
        if rc != 0:
            # Branch was pruned (likely by the sibling ship that already
            # cleaned it up). Not in our way.
            continue
        rc, _, _ = await _git(
            workspace,
            "merge-base",
            "--is-ancestor",
            item.branch,
            default_branch,
        )
        if rc != 0:
            return False
    return True


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
