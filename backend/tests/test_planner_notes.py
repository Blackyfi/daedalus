"""Project-notes playbook injection into planning (IMPROVEMENTS #19)."""
from __future__ import annotations

from daedalus.planning.planner import _format_notes


def test_format_notes_renders_title_and_body():
    out = _format_notes([{"title": "Convention", "body": "Use snake_case."}])
    assert "Convention" in out
    assert "Use snake_case." in out


def test_format_notes_handles_missing_title():
    out = _format_notes([{"body": "Body only."}])
    assert "Body only." in out


def test_format_notes_empty():
    assert _format_notes([]) == ""


def test_format_notes_caps_count():
    notes = [{"title": f"n{i}", "body": "x"} for i in range(50)]
    out = _format_notes(notes, max_count=20)
    assert out.count("\n- ") + 1 == 20  # 20 entries rendered
