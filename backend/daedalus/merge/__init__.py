"""Batch-merge of completed task branches onto an integration branch."""
from daedalus.merge.executor import BatchResult, execute_batch
from daedalus.merge.planner import BranchCandidate, BranchPlan, plan_batch, select_candidates
from daedalus.merge.resolution import (
    ResolutionStep,
    reconcile_resolution_states,
    resolve_next_conflict,
)
from daedalus.merge.ship import ShipResult, UndoResult, ship_batch, undo_ship

__all__ = [
    "BatchResult",
    "BranchCandidate",
    "BranchPlan",
    "ResolutionStep",
    "ShipResult",
    "UndoResult",
    "execute_batch",
    "plan_batch",
    "reconcile_resolution_states",
    "resolve_next_conflict",
    "select_candidates",
    "ship_batch",
    "undo_ship",
]
