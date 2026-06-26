"""Bug #1 regression: batch (exit_code) stdin connectors must get EOF, while
interactive (regex/tool_call) agents that keep reading stdin must not."""
from __future__ import annotations

from daedalus.talos.runner import _should_close_stdin


def _spec(input_kind="stdin_prompt", done_kind="exit_code", close_stdin=None):
    inp = {"kind": input_kind}
    if close_stdin is not None:
        inp["close_stdin"] = close_stdin
    return {"input_format": inp, "done_signal": {"kind": done_kind}}


def test_exit_code_stdin_connector_closes_by_default():
    # shell-demo / codex shape
    assert _should_close_stdin(_spec(done_kind="exit_code")) is True


def test_regex_stdin_connector_stays_open():
    # claude-code / qwen shape — keeps reading stdin while running
    assert _should_close_stdin(_spec(done_kind="regex")) is False


def test_tool_call_stdin_connector_stays_open():
    assert _should_close_stdin(_spec(done_kind="tool_call")) is False


def test_explicit_close_stdin_true_overrides_regex():
    assert _should_close_stdin(_spec(done_kind="regex", close_stdin=True)) is True


def test_explicit_close_stdin_false_overrides_exit_code():
    assert _should_close_stdin(_spec(done_kind="exit_code", close_stdin=False)) is False


def test_file_prompt_never_closes():
    assert _should_close_stdin(_spec(input_kind="file_prompt")) is False
