"""Regression: the resolution reconciler must not clobber a shipped batch.

`reconcile_resolution_states` runs on every merge-batch status poll. It used to
end with an unconditional `batch.state = awaiting_review` whenever no conflicts
were pending — which reset a freshly *shipped* batch back to awaiting_review on
the next poll (shipped_at stayed set, state regressed), silently breaking
one-click ship-undo (undo refuses unless state == shipped). The fix guards the
advance block to the resolution lifecycle only. Proven live; pinned here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from daedalus.db.models import MergeBatchState
from daedalus.merge.resolution import reconcile_resolution_states


class _Result:
    def __init__(self, items): self._items = list(items)
    def scalars(self): return self
    def __iter__(self): return iter(self._items)
    def all(self): return list(self._items)


class _FakeDB:
    def __init__(self, batch, items=()):
        self._batch = batch
        self._items = list(items)
        self.execute_calls = 0

    async def get(self, _model, _id):
        return self._batch

    async def execute(self, _query):
        self.execute_calls += 1
        # 1st call: items still in a resolution state (none here).
        # 2nd call (only past the guard): all items in the batch.
        return _Result([] if self.execute_calls == 1 else self._items)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state",
    [
        MergeBatchState.shipped,
        MergeBatchState.shipping,
        MergeBatchState.failed,
        MergeBatchState.aborted,
    ],
)
async def test_reconcile_leaves_terminal_states_untouched(terminal_state) -> None:
    batch = SimpleNamespace(state=terminal_state, integration_worktree="/x")
    db = _FakeDB(batch)
    changed = await reconcile_resolution_states(db, uuid.uuid4())
    assert changed == []
    assert batch.state is terminal_state  # not downgraded
    # Returned at the guard, before the "advance the batch" query.
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_reconcile_still_advances_a_resolving_batch() -> None:
    # The legitimate path the reconciler owns: a resolving batch with no pending
    # conflicts advances to awaiting_review.
    batch = SimpleNamespace(state=MergeBatchState.resolving, integration_worktree="/x")
    db = _FakeDB(batch, items=[])
    await reconcile_resolution_states(db, uuid.uuid4())
    assert batch.state is MergeBatchState.awaiting_review
    assert db.execute_calls == 2  # passed the guard, ran the items_all query
