"""Planner — pick candidate branches and dry-run a merge against the project's
default branch. Pure inspection: no working-tree mutation, no commits. The
executor consumes the plan."""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.db.models import ArgusReport, Run, RunKind, Task, TaskStatus, Verdict

logger = structlog.get_logger()

Category = Literal["clean", "conflict", "empty", "already-merged", "missing-branch", "missing-run"]


@dataclass
class BranchCandidate:
    task_id: uuid.UUID
    task_title: str
    run_id: uuid.UUID | None
    branch: str
    argus_verdict: str | None  # "pass" | "partial" | "fail" | None


@dataclass
class BranchPlan:
    candidate: BranchCandidate
    category: Category
    conflicting_files: list[str] = field(default_factory=list)
    commits_ahead: int = 0
    files_changed: int = 0


@dataclass
class MergePlan:
    project_id: uuid.UUID
    project_name: str
    workspace_path: str
    default_branch: str
    integration_branch: str
    plans: list[BranchPlan]


async def select_candidates(
    db: AsyncSession,
    project_id: uuid.UUID,
    require_argus_pass: bool = True,
    only_task_ids: list[uuid.UUID] | None = None,
) -> list[BranchCandidate]:
    """Pull all `done` tasks for the project, attach branch + argus verdict.

    `require_argus_pass=True` keeps tasks with verdict ∈ {pass, NULL}; passes/None
    are kept (None = argus disabled or never ran). Anything else is dropped.
    """
    stmt = (
        select(Task)
        .where(Task.project_id == project_id, Task.status == TaskStatus.done)
        .order_by(Task.created_at.asc())
    )
    if only_task_ids:
        stmt = stmt.where(Task.id.in_(only_task_ids))
    tasks = list((await db.execute(stmt)).scalars())

    candidates: list[BranchCandidate] = []
    for task in tasks:
        run_res = await db.execute(
            select(Run)
            .where(Run.task_id == task.id, Run.kind == RunKind.task)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        latest_run = run_res.scalar_one_or_none()
        run_id = latest_run.id if latest_run is not None else None

        verdict_value: str | None = None
        if latest_run is not None:
            v_res = await db.execute(
                select(ArgusReport)
                .where(ArgusReport.run_id == latest_run.id)
                .order_by(ArgusReport.created_at.desc())
                .limit(1)
            )
            ar = v_res.scalar_one_or_none()
            if ar is not None:
                verdict_value = ar.verdict.value if isinstance(ar.verdict, Verdict) else str(ar.verdict)

        if require_argus_pass and verdict_value not in (None, "pass"):
            continue

        branch = f"daedalus-run-{run_id}" if run_id is not None else ""
        candidates.append(
            BranchCandidate(
                task_id=task.id,
                task_title=task.title,
                run_id=run_id,
                branch=branch,
                argus_verdict=verdict_value,
            )
        )
    return candidates


async def plan_batch(
    workspace_path: str,
    default_branch: str,
    candidates: list[BranchCandidate],
    integration_branch: str,
) -> list[BranchPlan]:
    """Categorize each candidate without touching the working tree."""
    out: list[BranchPlan] = []
    for c in candidates:
        out.append(await _classify(workspace_path, default_branch, c))
    return out


async def _classify(workspace_path: str, default_branch: str, c: BranchCandidate) -> BranchPlan:
    if c.run_id is None or not c.branch:
        return BranchPlan(candidate=c, category="missing-run")

    if not await _branch_exists(workspace_path, c.branch):
        return BranchPlan(candidate=c, category="missing-branch")

    ahead = await _commits_ahead(workspace_path, default_branch, c.branch)
    files_changed = await _files_changed(workspace_path, default_branch, c.branch)

    if ahead == 0 and files_changed == 0:
        # The branch contributes nothing main doesn't already have. Two
        # reasons that look identical from is-ancestor alone:
        #   (1) Real merge: a merge commit on main has this branch's tip
        #       as a second parent. The agent's work landed.
        #   (2) Empty: the agent never committed; branch tip == its base
        #       (often main's initial commit). The work — if any — exists
        #       only as uncommitted edits in the live workspace.
        # Detect the difference by walking main's merge commits.
        if await _was_merged_via_merge_commit(workspace_path, default_branch, c.branch):
            return BranchPlan(candidate=c, category="already-merged")
        return BranchPlan(candidate=c, category="empty")

    rc, conflicts = await _merge_tree_dry_run(workspace_path, default_branch, c.branch)
    if rc == 0:
        return BranchPlan(candidate=c, category="clean", commits_ahead=ahead, files_changed=files_changed)
    return BranchPlan(
        candidate=c,
        category="conflict",
        conflicting_files=conflicts,
        commits_ahead=ahead,
        files_changed=files_changed,
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


async def _branch_exists(workspace_path: str, branch: str) -> bool:
    rc, _, _ = await _git(workspace_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return rc == 0


async def _is_ancestor(workspace_path: str, ancestor: str, descendant: str) -> bool:
    rc, _, _ = await _git(workspace_path, "merge-base", "--is-ancestor", ancestor, descendant)
    return rc == 0


async def _was_merged_via_merge_commit(workspace_path: str, default_branch: str, branch: str) -> bool:
    """True iff some merge commit on `default_branch` has `branch`'s tip as
    a second parent — the only reliable proof a branch was actually merged
    in (vs. just sitting at an ancestor commit because the agent never
    committed and the operator landed work on main directly).

    Bounded to the most recent 1000 merge commits on `default_branch` so
    this stays cheap on huge repos."""
    rc, tip, _ = await _git(workspace_path, "rev-parse", "--verify", branch)
    if rc != 0 or not tip.strip():
        return False
    target = tip.strip()
    rc, out, _ = await _git(
        workspace_path, "log", default_branch, "--merges", "--max-count=1000", "--pretty=%P"
    )
    if rc != 0:
        return False
    for line in out.splitlines():
        parents = line.strip().split()
        if len(parents) >= 2 and target in parents[1:]:
            return True
    return False


async def _commits_ahead(workspace_path: str, base: str, branch: str) -> int:
    rc, out, _ = await _git(workspace_path, "rev-list", "--count", f"{base}..{branch}")
    if rc != 0:
        return 0
    try:
        return int(out.strip())
    except ValueError:
        return 0


async def _files_changed(workspace_path: str, base: str, branch: str) -> int:
    rc, out, _ = await _git(workspace_path, "diff", "--name-only", f"{base}...{branch}")
    if rc != 0:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


_CONFLICT_LINE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(.+)$")


async def _merge_tree_dry_run(workspace_path: str, base: str, branch: str) -> tuple[int, list[str]]:
    """Use `git merge-tree --write-tree` to dry-run the merge.

    Modern git (2.38+) returns exit 0 on a clean merge with the resulting tree
    OID on stdout; on conflict it returns non-zero and writes the conflicted-
    file list as `<mode> <oid> <stage>\\t<path>` lines after the tree OID. We
    recover the unique conflicted file paths.
    """
    rc, out, _ = await _git(
        workspace_path, "merge-tree", "--write-tree", "--no-messages", base, branch
    )
    if rc == 0:
        return (0, [])
    files: set[str] = set()
    # Skip the first line (tree OID); subsequent lines until a blank line list
    # conflicted entries in `<mode> <oid> <stage>\t<path>` form.
    for line in out.splitlines()[1:]:
        if not line.strip():
            break
        if "\t" in line:
            files.add(line.split("\t", 1)[1].strip())
    return (rc, sorted(files))
