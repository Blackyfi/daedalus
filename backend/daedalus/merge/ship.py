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
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import (
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
    return ShipResult(
        state="shipped",
        integration_branch=batch.integration_branch,
        default_branch=default_branch,
        pruned_branches=pruned,
        removed_worktree=removed,
    )


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
