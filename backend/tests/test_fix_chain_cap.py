"""Coverage for the fix-loop chain-depth cap in hermes.scheduler.

The cap used to be per-task — Fix-child rows reset fix_loop_count=0, so
chains grew unbounded. The patch walks parent_task_id to count chain
depth from the original task, and stops the spawn (plus tags the root
"manual-review") when depth >= project.max_fix_loops.
"""
from __future__ import annotations

import uuid

import pytest

from daedalus.db.models import Task, TaskPriority, TaskStatus
from daedalus.hermes.scheduler import HermesScheduler


def _task(parent_id: uuid.UUID | None = None, tags: list[str] | None = None) -> Task:
    t = Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        parent_task_id=parent_id,
        title="t",
        description="",
        acceptance_criteria="",
        status=TaskStatus.backlog,
        priority=TaskPriority.P2,
        profile="confirm",
        tags=list(tags or []),
        depends_on=[],
        fix_loop_count=0,
    )
    return t


class _FakeSession:
    def __init__(self, tasks: list[Task]) -> None:
        self._by_id = {t.id: t for t in tasks}

    async def get(self, model, ident):
        if model is Task:
            return self._by_id.get(ident)
        return None


@pytest.mark.asyncio
async def test_chain_depth_returns_zero_for_root_task() -> None:
    sched = HermesScheduler()
    root = _task()
    session = _FakeSession([root])
    depth = await sched._fix_chain_depth(session, root)
    assert depth == 0


@pytest.mark.asyncio
async def test_chain_depth_counts_each_fix_child() -> None:
    """root → fix1 → fix2 → fix3  ⇒ depth(fix3) == 3."""
    sched = HermesScheduler()
    root = _task()
    fix1 = _task(parent_id=root.id)
    fix2 = _task(parent_id=fix1.id)
    fix3 = _task(parent_id=fix2.id)
    session = _FakeSession([root, fix1, fix2, fix3])

    assert await sched._fix_chain_depth(session, root) == 0
    assert await sched._fix_chain_depth(session, fix1) == 1
    assert await sched._fix_chain_depth(session, fix2) == 2
    assert await sched._fix_chain_depth(session, fix3) == 3


@pytest.mark.asyncio
async def test_chain_root_returns_original_task() -> None:
    """root.id is returned for every node in the chain."""
    sched = HermesScheduler()
    root = _task()
    fix1 = _task(parent_id=root.id)
    fix2 = _task(parent_id=fix1.id)
    session = _FakeSession([root, fix1, fix2])

    assert (await sched._fix_chain_root(session, root)).id == root.id
    assert (await sched._fix_chain_root(session, fix1)).id == root.id
    assert (await sched._fix_chain_root(session, fix2)).id == root.id


@pytest.mark.asyncio
async def test_chain_depth_handles_orphan_parent_gracefully() -> None:
    """If parent_task_id resolves to None (parent deleted), stop counting."""
    sched = HermesScheduler()
    fix = _task(parent_id=uuid.uuid4())  # parent doesn't exist in session
    session = _FakeSession([fix])
    assert await sched._fix_chain_depth(session, fix) == 0


@pytest.mark.asyncio
async def test_chain_depth_bounded_against_cycles() -> None:
    """If something pathological creates a parent cycle, the walk caps at 50
    iterations rather than infinite-looping."""
    sched = HermesScheduler()
    a = _task()
    b = _task(parent_id=a.id)
    # Force a cycle: a.parent → b, b.parent → a
    a.parent_task_id = b.id
    session = _FakeSession([a, b])
    depth = await sched._fix_chain_depth(session, a)
    assert depth == 50
