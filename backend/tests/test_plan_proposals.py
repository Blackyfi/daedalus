"""Smoke tests for the Plan Review flow (proposal generation + confirm)."""
from __future__ import annotations

import pytest

# These tests need the model + pydantic schemas only — they exercise the
# pure-Python helpers and Pydantic validation, not the async DB session.


def test_plan_proposal_status_enum_values() -> None:
    from daedalus.db.models import PlanProposalStatus

    assert {member.value for member in PlanProposalStatus} == {"pending", "confirmed", "discarded"}


def test_proposed_task_round_trips_through_plan_confirm_payload() -> None:
    pytest.importorskip("pydantic")
    from daedalus.api.schemas import PlanConfirm, ProposedTask

    payload = {
        "proposed_tasks": [
            {
                "title": "Wire up the dashboard",
                "description": "Render task board live updates",
                "acceptance_criteria": "Cards reorder when status changes",
                "priority": "P1",
                "depends_on": [],
                "tags": ["frontend"],
            }
        ],
        "rationale": "Pulled from the open Idea Box",
        "archive_source_ideas": True,
    }

    confirm = PlanConfirm.model_validate(payload)

    assert confirm.proposed_tasks is not None
    assert len(confirm.proposed_tasks) == 1
    assert isinstance(confirm.proposed_tasks[0], ProposedTask)
    assert confirm.proposed_tasks[0].priority.value == "P1"
    assert confirm.archive_source_ideas is True


def test_internal_idea_to_task_fields_picks_acceptance_marker() -> None:
    from daedalus.api.routes.internal import _idea_to_task_fields

    title, desc, acc = _idea_to_task_fields(
        "Add idea box\nNeeds drag/drop\nAcceptance: ideas reorder by drag"
    )
    assert title == "Add idea box"
    assert "drag" in desc.lower()
    assert "drag" in acc.lower()
