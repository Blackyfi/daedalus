"""LLM client + Argus + planning unit tests with no network calls."""
from __future__ import annotations

import pytest

from daedalus.argus import verify_run
from daedalus.llm.client import extract_json
from daedalus.planning import build_proposal
from daedalus.planning.planner import _idea_to_task_fields


def test_extract_json_handles_bare_object():
    assert extract_json('{"verdict": "pass"}') == {"verdict": "pass"}


def test_extract_json_handles_fenced_block():
    text = 'Here is the verdict:\n```json\n{"verdict": "fail"}\n```\nthanks.'
    assert extract_json(text) == {"verdict": "fail"}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("no json here at all") is None


def test_idea_to_task_fields_round_trip_via_planner():
    title, desc, accept = _idea_to_task_fields("Add login\nNeed an SSO page\nacceptance: SSO works")
    assert title == "Add login"
    assert "SSO page" in desc
    assert accept == "SSO works"


@pytest.mark.asyncio
async def test_verify_run_falls_back_when_llm_unreachable(monkeypatch):
    # Force the LLM client to error so we exercise the deterministic fallback.
    from daedalus.argus import verifier as argus_mod
    from daedalus.llm import LLMError
    from daedalus.llm import client as llm_client_mod

    class FakeClient:
        verifier_model = "x"

        async def chat_json(self, *_a, **_kw):
            raise LLMError("offline")

    monkeypatch.setattr(argus_mod, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(llm_client_mod, "get_llm_client", lambda: FakeClient())

    verdict = await verify_run(
        task_title="t",
        task_description="d",
        acceptance_criteria="a",
        verify_commands=["pytest -q"],
        diff_text="",
        verify_output="",
        verify_exit_code=0,
    )
    assert verdict.verdict == "pass"

    verdict_fail = await verify_run(
        task_title="t",
        task_description="d",
        acceptance_criteria="a",
        verify_commands=["pytest -q"],
        diff_text="",
        verify_output="boom",
        verify_exit_code=1,
    )
    assert verdict_fail.verdict == "fail"
    assert verdict_fail.findings


@pytest.mark.asyncio
async def test_build_proposal_falls_back_when_llm_unreachable(monkeypatch):
    from daedalus.llm import LLMError
    from daedalus.planning import planner

    class FakeClient:
        async def chat_json(self, *_a, **_kw):
            raise LLMError("offline")

    monkeypatch.setattr(planner, "get_llm_client", lambda: FakeClient())

    proposal = await build_proposal(
        project_name="p",
        project_description="d",
        workspace_path="/tmp/does-not-exist",
        git_default_branch="main",
        default_connector_id="claude-code-confirm",
        available_connector_ids=["claude-code-confirm"],
        existing_tasks=[],
        ideas=[
            {"id": "id-1", "text": "Add a CSV importer\nacceptance: handles 100k rows", "tags": ["data"]},
            {"id": "id-2", "text": "Refactor settings module", "tags": []},
        ],
    )
    assert len(proposal.proposed_tasks) == 2
    assert proposal.proposed_tasks[0].title == "Add a CSV importer"
    assert proposal.proposed_tasks[0].acceptance_criteria == "handles 100k rows"
    assert "planned-from-idea" in proposal.proposed_tasks[0].tags
    assert "LLM unavailable" in proposal.rationale
