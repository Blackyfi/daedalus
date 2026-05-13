"""Fixes for the merge-batch planner defects surfaced on 2026-05-13.

Defect #1 — No claim-check before create.
  Three "create batch" clicks inside 14 minutes produced batches 9faf89e0,
  426f21d5, 71d3732c over the same 34/35-task set. Only 71d3732c shipped;
  the other two lingered in awaiting_review.

Defect #2 — No reconcile after ship.
  When 71d3732c shipped, 9faf89e0/426f21d5 should have auto-aborted
  (their items were now reachable from main via the sibling). Instead
  they stayed live as ship-attempt no-ops.

These tests exercise both fixes plus the end-to-end scripted scenario
required by the task's acceptance criteria.
"""
from __future__ import annotations

import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

# ── fake session ────────────────────────────────────────────────────────
#
# `aiosqlite` isn't installable in the sandbox, so we can't spin up a real
# AsyncSession. Instead we implement just enough of the protocol to back
# the three queries this module's code actually issues:
#
#   (A) find_claim_conflicts:
#       SELECT mbi.task_id, mbi.batch_id
#       FROM merge_batch_items JOIN merge_batches ON ...
#       WHERE mb.project_id = :p AND mb.state IN (...) AND mbi.task_id IN (...)
#
#   (B) _reconcile_superseded_batches (outer query):
#       SELECT * FROM merge_batches
#       WHERE project_id = :p AND id != :sid AND state IN (...)
#
#   (C) _reconcile_superseded_batches (per-batch query):
#       SELECT * FROM merge_batch_items WHERE batch_id = :bid
#
# The fake routes each call to the right in-memory filter by inspecting
# the compiled SQL string.


class _FakeResult:
    """Mimics the parts of `sqlalchemy.engine.Result` our code uses."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self):
        # Single-column SELECTs use .scalars(); for our (A) query each row
        # is a 2-tuple, which never goes through scalars() — only queries
        # (B) and (C) do. Those have row objects, not tuples.
        return _FakeScalars(self._rows)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self) -> None:
        self.batches: dict[uuid.UUID, Any] = {}
        self.items: list[Any] = []
        self.added: list[Any] = []
        self.flush_count = 0

    # --- session protocol the code under test uses ----------------------

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        # Mirror SQLAlchemy: defaults applied at flush time. The executor
        # sets every column it cares about explicitly, so we just track
        # the object.
        from daedalus.db.models import MergeBatch, MergeBatchItem

        if isinstance(obj, MergeBatch):
            self.batches[obj.id] = obj
        elif isinstance(obj, MergeBatchItem):
            self.items.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        pass

    async def refresh(self, _obj: Any) -> None:
        pass

    async def get(self, model: Any, ident: Any) -> Any | None:
        from daedalus.db.models import MergeBatch

        if model is MergeBatch:
            return self.batches.get(ident)
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        compiled = stmt.compile()
        params = dict(compiled.params)

        from daedalus.db.models import OPEN_MERGE_BATCH_STATES

        # (A) — claim-conflict query: task_id+batch_id from a JOIN.
        if (
            "merge_batch_items.task_id" in sql
            and "merge_batch_items.batch_id" in sql
            and "JOIN merge_batches" in sql
        ):
            project_id = params.get("project_id_1")
            states = list(params.get("state_1") or [])
            task_ids = list(params.get("task_id_1") or [])
            rows: list[tuple[uuid.UUID, uuid.UUID]] = []
            for item in self.items:
                batch = self.batches.get(item.batch_id)
                if batch is None:
                    continue
                if batch.project_id != project_id:
                    continue
                if batch.state not in states:
                    continue
                if item.task_id not in task_ids:
                    continue
                rows.append((item.task_id, item.batch_id))
            return _FakeResult(rows)

        # (B) — open-batch list for reconcile.
        if (
            "FROM merge_batches" in sql
            and "merge_batches.id != :id_1" in sql
            and "merge_batches.project_id = :project_id_1" in sql
        ):
            project_id = params.get("project_id_1")
            excluded_id = params.get("id_1")
            states = list(params.get("state_1") or list(OPEN_MERGE_BATCH_STATES))
            rows = [
                b
                for b in self.batches.values()
                if b.project_id == project_id and b.id != excluded_id and b.state in states
            ]
            return _FakeResult(rows)

        # (C) — items for a specific batch.
        if (
            "FROM merge_batch_items" in sql
            and "merge_batch_items.batch_id = :batch_id_1" in sql
            and "JOIN" not in sql
        ):
            batch_id = params.get("batch_id_1")
            rows = [i for i in self.items if i.batch_id == batch_id]
            return _FakeResult(rows)

        raise AssertionError(f"_FakeSession got an unexpected query:\n{sql}\nparams={params}")


# ── helpers ─────────────────────────────────────────────────────────────


def _git(cwd: str, *args: str) -> str:
    """Sync git helper — only used from test setup, not from code under test."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@dataclass
class _PlanStub:
    """Minimal stand-in for daedalus.merge.planner.BranchPlan that satisfies
    the few attributes executor.execute_batch reads before raising."""

    candidate: SimpleNamespace
    category: str = "clean"
    conflicting_files: list[str] = field(default_factory=list)
    commits_ahead: int = 1
    files_changed: int = 1


def _make_plan(task_id: uuid.UUID) -> Any:
    from daedalus.merge.planner import BranchCandidate, BranchPlan

    cand = BranchCandidate(
        task_id=task_id,
        task_title=f"task-{task_id}",
        run_id=uuid.uuid4(),
        branch=f"daedalus-run-{uuid.uuid4()}",
        argus_verdict="pass",
    )
    return BranchPlan(candidate=cand, category="clean", commits_ahead=1, files_changed=1)


def _seed_open_batch_over(
    db: _FakeSession,
    project_id: uuid.UUID,
    task_ids: list[uuid.UUID],
    state: Any = None,
) -> uuid.UUID:
    """Pre-populate an open MergeBatch + items for `task_ids` so the next
    create_batch attempt collides with it."""
    from daedalus.db.models import (
        MergeBatch,
        MergeBatchItem,
        MergeBatchState,
        MergeItemCategory,
        MergeItemState,
    )

    batch_id = uuid.uuid4()
    batch = MergeBatch(
        id=batch_id,
        project_id=project_id,
        integration_branch=f"daedalus-merge-{batch_id}",
        integration_worktree=f"/tmp/{batch_id}",
        state=state or MergeBatchState.awaiting_review,
        require_argus_pass=True,
    )
    db.batches[batch_id] = batch
    for tid in task_ids:
        db.items.append(
            MergeBatchItem(
                id=uuid.uuid4(),
                batch_id=batch_id,
                task_id=tid,
                source_run_id=uuid.uuid4(),
                branch=f"daedalus-run-{uuid.uuid4()}",
                category=MergeItemCategory.clean,
                state=MergeItemState.merged,
                conflicting_files=[],
                commits_ahead=1,
                files_changed=1,
            )
        )
    return batch_id


# ── exception shape ─────────────────────────────────────────────────────


def test_claim_conflict_exception_payload_is_useful() -> None:
    """The exception must carry the task→batch map so the API can render a
    pointer-to-existing-batch error, not just "rejected"."""
    from daedalus.merge import MergeBatchClaimConflict

    t1, t2, b1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    exc = MergeBatchClaimConflict({t1: b1, t2: b1})
    assert exc.conflicts == {t1: b1, t2: b1}
    msg = str(exc)
    assert "already belong to an open merge batch" in msg
    assert str(b1) in msg


# ── find_claim_conflicts ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_claim_conflicts_detects_overlap_with_open_batch() -> None:
    from daedalus.merge import find_claim_conflicts

    db = _FakeSession()
    project_id = uuid.uuid4()
    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    existing = _seed_open_batch_over(db, project_id, [t1, t2])

    # Asking about [t1, t2, t3]: t1 and t2 are claimed by `existing`, t3
    # is free.
    conflicts = await find_claim_conflicts(db, project_id, [t1, t2, t3])
    assert conflicts == {t1: existing, t2: existing}


@pytest.mark.asyncio
async def test_find_claim_conflicts_ignores_terminal_batches() -> None:
    """Shipped/failed/aborted batches release their claim — those tasks
    must be re-batchable."""
    from daedalus.db.models import MergeBatchState
    from daedalus.merge import find_claim_conflicts

    db = _FakeSession()
    project_id = uuid.uuid4()
    t1 = uuid.uuid4()

    _seed_open_batch_over(db, project_id, [t1], state=MergeBatchState.shipped)
    _seed_open_batch_over(db, project_id, [t1], state=MergeBatchState.aborted)
    _seed_open_batch_over(db, project_id, [t1], state=MergeBatchState.failed)

    conflicts = await find_claim_conflicts(db, project_id, [t1])
    assert conflicts == {}


@pytest.mark.asyncio
async def test_find_claim_conflicts_is_project_scoped() -> None:
    """An open batch in a *different* project must not claim our task."""
    from daedalus.merge import find_claim_conflicts

    db = _FakeSession()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    t1 = uuid.uuid4()
    _seed_open_batch_over(db, project_a, [t1])

    conflicts = await find_claim_conflicts(db, project_b, [t1])
    assert conflicts == {}


# ── execute_batch claim-check ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_batch_raises_when_candidates_claimed() -> None:
    """The planner-claim guard in execute_batch must fire before any git
    side effects happen: no worktree, no MergeBatch row, no DB flush."""
    from daedalus.merge import MergeBatchClaimConflict, execute_batch

    db = _FakeSession()
    project_id = uuid.uuid4()
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    existing = _seed_open_batch_over(db, project_id, [t1, t2])
    pre_added = list(db.added)

    plans = [_make_plan(t1), _make_plan(t2)]
    with pytest.raises(MergeBatchClaimConflict) as info:
        await execute_batch(
            db=db,
            project_id=project_id,
            workspace_path="/tmp/does-not-matter",
            default_branch="main",
            plans=plans,
            verify_commands=[],
            require_argus_pass=True,
            created_by_user_id=None,
        )

    # Both candidates point at the same existing batch.
    assert info.value.conflicts == {t1: existing, t2: existing}
    # And nothing new was persisted — no batch / item rows added past the
    # seeded ones.
    assert db.added == pre_added


# ── _all_items_in_main against a real git repo ──────────────────────────


def _run(*args: str, cwd: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo() -> Any:
    """Two-branch repo:

      main:    A --- M    (M = merge of feature-merged into main)
                 \\  /
      feature-merged: B
      feature-pending: C   (never merged; has new commits)
      empty-branch:    points at A (no new commits)

    Yields the repo path. Cleaned up on teardown."""
    repo = tempfile.mkdtemp(prefix="dae-test-repo-")
    try:
        _run("git", "init", "-q", "-b", "main", cwd=repo)
        _run("git", "config", "user.email", "t@t", cwd=repo)
        _run("git", "config", "user.name", "T", cwd=repo)
        _run("git", "commit", "--allow-empty", "-m", "A", cwd=repo)

        # feature-merged: branch off, add commit, then merge into main.
        _run("git", "checkout", "-q", "-b", "feature-merged", cwd=repo)
        _run("git", "commit", "--allow-empty", "-m", "B", cwd=repo)
        _run("git", "checkout", "-q", "main", cwd=repo)
        _run(
            "git",
            "merge",
            "--no-ff",
            "-m",
            "merge feature-merged",
            "feature-merged",
            cwd=repo,
        )

        # feature-pending: branched from main *before* the merge, has a
        # commit that never landed.
        _run("git", "checkout", "-q", "-b", "feature-pending", "HEAD~1", cwd=repo)
        _run("git", "commit", "--allow-empty", "-m", "C", cwd=repo)
        _run("git", "checkout", "-q", "main", cwd=repo)

        # empty-branch: tip == main but no new commits at all.
        _run("git", "branch", "empty-branch", "main", cwd=repo)
        yield repo
    finally:
        # Best-effort cleanup.
        import shutil

        shutil.rmtree(repo, ignore_errors=True)


@pytest.mark.asyncio
async def test_all_items_in_main_true_when_all_branches_reachable(git_repo: str) -> None:
    from daedalus.merge.ship import _all_items_in_main

    items = [
        SimpleNamespace(branch="feature-merged"),
        SimpleNamespace(branch="empty-branch"),
        # A missing branch (already cleaned up by a sibling ship) doesn't
        # count against supersession.
        SimpleNamespace(branch="branch-that-was-deleted"),
        # And an item that never had a branch at all (missing-run) is
        # likewise harmless.
        SimpleNamespace(branch=""),
    ]
    assert await _all_items_in_main(git_repo, "main", items) is True


@pytest.mark.asyncio
async def test_all_items_in_main_false_when_branch_has_new_commits(git_repo: str) -> None:
    from daedalus.merge.ship import _all_items_in_main

    items = [
        SimpleNamespace(branch="feature-merged"),
        SimpleNamespace(branch="feature-pending"),  # has commit C not on main
    ]
    assert await _all_items_in_main(git_repo, "main", items) is False


# ── _reconcile_superseded_batches ───────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_aborts_superseded_sibling(git_repo: str) -> None:
    """A sibling batch over the same already-merged branches must be
    flipped to `aborted` with a clear error pointing at the shipper."""
    from daedalus.db.models import (
        MergeBatch,
        MergeBatchItem,
        MergeBatchState,
        MergeItemCategory,
        MergeItemState,
    )
    from daedalus.merge.ship import _reconcile_superseded_batches

    db = _FakeSession()
    project_id = uuid.uuid4()
    shipped_id = uuid.uuid4()
    sibling_id = uuid.uuid4()

    # The shipped batch — state is whatever; reconcile excludes it by id.
    db.batches[shipped_id] = MergeBatch(
        id=shipped_id,
        project_id=project_id,
        integration_branch="daedalus-merge-shipped",
        integration_worktree="/tmp/shipped",
        state=MergeBatchState.shipped,
        require_argus_pass=True,
    )

    # The sibling — open, over a branch that's already in main.
    db.batches[sibling_id] = MergeBatch(
        id=sibling_id,
        project_id=project_id,
        integration_branch="daedalus-merge-sibling",
        integration_worktree="/tmp/sibling",
        state=MergeBatchState.awaiting_review,
        require_argus_pass=True,
    )
    db.items.append(
        MergeBatchItem(
            id=uuid.uuid4(),
            batch_id=sibling_id,
            task_id=uuid.uuid4(),
            source_run_id=uuid.uuid4(),
            branch="feature-merged",
            category=MergeItemCategory.clean,
            state=MergeItemState.merged,
            conflicting_files=[],
            commits_ahead=1,
            files_changed=1,
        )
    )

    superseded = await _reconcile_superseded_batches(
        db,
        project_id=project_id,
        shipped_batch_id=shipped_id,
        workspace=git_repo,
        default_branch="main",
    )

    assert superseded == [sibling_id]
    sibling = db.batches[sibling_id]
    assert sibling.state == MergeBatchState.aborted
    assert sibling.error is not None
    assert str(shipped_id) in sibling.error
    assert "Superseded by batch" in sibling.error


@pytest.mark.asyncio
async def test_reconcile_leaves_unrelated_open_batch_alone(git_repo: str) -> None:
    """A sibling batch with un-merged work must NOT be aborted."""
    from daedalus.db.models import (
        MergeBatch,
        MergeBatchItem,
        MergeBatchState,
        MergeItemCategory,
        MergeItemState,
    )
    from daedalus.merge.ship import _reconcile_superseded_batches

    db = _FakeSession()
    project_id = uuid.uuid4()
    shipped_id = uuid.uuid4()
    sibling_id = uuid.uuid4()

    db.batches[shipped_id] = MergeBatch(
        id=shipped_id,
        project_id=project_id,
        integration_branch="daedalus-merge-shipped",
        integration_worktree="/tmp/shipped",
        state=MergeBatchState.shipped,
        require_argus_pass=True,
    )
    db.batches[sibling_id] = MergeBatch(
        id=sibling_id,
        project_id=project_id,
        integration_branch="daedalus-merge-sibling",
        integration_worktree="/tmp/sibling",
        state=MergeBatchState.awaiting_review,
        require_argus_pass=True,
    )
    # Mix: one merged-into-main + one still pending.
    db.items.append(
        MergeBatchItem(
            id=uuid.uuid4(),
            batch_id=sibling_id,
            task_id=uuid.uuid4(),
            source_run_id=uuid.uuid4(),
            branch="feature-merged",
            category=MergeItemCategory.clean,
            state=MergeItemState.merged,
            conflicting_files=[],
            commits_ahead=1,
            files_changed=1,
        )
    )
    db.items.append(
        MergeBatchItem(
            id=uuid.uuid4(),
            batch_id=sibling_id,
            task_id=uuid.uuid4(),
            source_run_id=uuid.uuid4(),
            branch="feature-pending",  # NOT reachable from main
            category=MergeItemCategory.clean,
            state=MergeItemState.merged,
            conflicting_files=[],
            commits_ahead=1,
            files_changed=1,
        )
    )

    superseded = await _reconcile_superseded_batches(
        db,
        project_id=project_id,
        shipped_batch_id=shipped_id,
        workspace=git_repo,
        default_branch="main",
    )

    assert superseded == []
    assert db.batches[sibling_id].state == MergeBatchState.awaiting_review


# ── full scripted scenario (the acceptance-criteria test) ───────────────


@pytest.mark.asyncio
async def test_scenario_create_a_ship_a_then_create_b_is_rejected() -> None:
    """The 2026-05-13 zombie sequence, as a script:

       1. Create batch A over tasks {t1, t2, t3}.
       2. Mark A as shipped (we stub the git side-effects; reconcile
          would catch siblings if any existed).
       3. **Before** A is marked shipped, attempt to create batch B over
          the same task set → must be rejected with a clear error
          pointing at A.

    Step 3 is the behavioural regression we're guarding against: the
    pre-fix code happily produced batch B (and C) over the live task
    set of A while A still owned them."""
    from daedalus.db.models import MergeBatchState
    from daedalus.merge import MergeBatchClaimConflict, execute_batch

    db = _FakeSession()
    project_id = uuid.uuid4()
    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # ── 1. Batch A exists and is awaiting_review (the state at which the
    # 2026-05-13 incident actually happened: 9faf89e0/426f21d5/71d3732c
    # were all created on top of an existing awaiting_review batch).
    batch_a = _seed_open_batch_over(
        db, project_id, [t1, t2, t3], state=MergeBatchState.awaiting_review
    )

    # ── 2. Attempt to create batch B over the same tasks. The pre-fix
    # code would happily create it; the fix must reject with a structured
    # error naming batch A.
    plans = [_make_plan(t1), _make_plan(t2), _make_plan(t3)]
    with pytest.raises(MergeBatchClaimConflict) as info:
        await execute_batch(
            db=db,
            project_id=project_id,
            workspace_path="/tmp/scenario",
            default_branch="main",
            plans=plans,
            verify_commands=[],
            require_argus_pass=True,
            created_by_user_id=None,
        )

    assert set(info.value.conflicts.keys()) == {t1, t2, t3}
    assert set(info.value.conflicts.values()) == {batch_a}
    msg = str(info.value)
    assert str(batch_a) in msg
    assert "open merge batch" in msg.lower()

    # ── 3. Cross-check by hand: only the seeded batch exists; the rejected
    # attempt did not leak a second MergeBatch row, an integration branch,
    # or a worktree path into the DB.
    assert len(db.batches) == 1
    assert batch_a in db.batches
    assert all(it.batch_id == batch_a for it in db.items)

    # ── 4. After we mark batch A terminal (shipped), the claim releases
    # and a fresh batch B over the same task set becomes legal again. We
    # only verify the claim-check returns clean — the rest of execute_batch
    # is the worktree/merge pipeline which needs a real workspace.
    db.batches[batch_a].state = MergeBatchState.shipped

    from daedalus.merge import find_claim_conflicts

    cleared = await find_claim_conflicts(db, project_id, [t1, t2, t3])
    assert cleared == {}
