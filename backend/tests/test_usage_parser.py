"""Unit tests for daedalus.connectors.usage."""
from __future__ import annotations

from daedalus.connectors.usage import UsageRecord, parse_usage


def test_no_parser_returns_empty() -> None:
    assert parse_usage("anything", None) == UsageRecord()
    assert parse_usage("anything", {}) == UsageRecord()


def test_unknown_kind_returns_empty() -> None:
    assert parse_usage("anything", {"kind": "bogus"}) == UsageRecord()


# ── claude ────────────────────────────────────────────────────────────────


def test_claude_prose_form() -> None:
    transcript = """
    Working on it...
    tokens used: 1,234 input, 567 output
    Done.
    """
    rec = parse_usage(transcript, {"kind": "claude"})
    assert rec.token_input == 1234
    assert rec.token_output == 567


def test_claude_json_blocks_aggregate() -> None:
    transcript = (
        '{"input_tokens": 100, "output_tokens": 50}\n'
        'something\n'
        '{"input_tokens": 200, "output_tokens": 75}\n'
    )
    rec = parse_usage(transcript, {"kind": "claude"})
    assert rec.token_input == 300
    assert rec.token_output == 125


def test_claude_with_cost_factors() -> None:
    transcript = '{"input_tokens": 1000, "output_tokens": 500}'
    rec = parse_usage(
        transcript,
        {
            "kind": "claude",
            "cost_per_input_micros": 3_000_000,
            "cost_per_output_micros": 15_000_000,
        },
    )
    assert rec.token_input == 1000
    assert rec.token_output == 500
    # 1000 * 3_000_000 / 1_000_000 + 500 * 15_000_000 / 1_000_000
    # = 3000 + 7500 = 10500 micros = $0.0105
    assert rec.cost_usd_micros == 10500


def test_claude_strips_ansi() -> None:
    transcript = (
        "\x1b[32mtokens used: 100 input, 50 output\x1b[0m"
    )
    rec = parse_usage(transcript, {"kind": "claude"})
    assert rec.token_input == 100
    assert rec.token_output == 50


# ── openai ────────────────────────────────────────────────────────────────


def test_openai_snake_case() -> None:
    transcript = '{"prompt_tokens": 42, "completion_tokens": 21}'
    rec = parse_usage(transcript, {"kind": "openai"})
    assert rec.token_input == 42
    assert rec.token_output == 21


def test_openai_camel_case() -> None:
    transcript = "promptTokens=99 completionTokens=33"
    rec = parse_usage(transcript, {"kind": "openai"})
    assert rec.token_input == 99
    assert rec.token_output == 33


def test_openai_aggregates_streamed_calls() -> None:
    transcript = (
        '"prompt_tokens": 10, "completion_tokens": 5\n'
        '"prompt_tokens": 20, "completion_tokens": 8'
    )
    rec = parse_usage(transcript, {"kind": "openai"})
    assert rec.token_input == 30
    assert rec.token_output == 13


# ── regex ─────────────────────────────────────────────────────────────────


def test_regex_custom() -> None:
    transcript = "IN=42 OUT=21\nIN=8 OUT=4"
    rec = parse_usage(
        transcript,
        {
            "kind": "regex",
            "input_pattern": r"IN=(\d+)",
            "output_pattern": r"OUT=(\d+)",
        },
    )
    assert rec.token_input == 50
    assert rec.token_output == 25


def test_regex_bad_pattern_swallowed() -> None:
    rec = parse_usage(
        "IN=42 OUT=21",
        {
            "kind": "regex",
            "input_pattern": r"IN=(\d+",  # unbalanced paren
            "output_pattern": r"OUT=(\d+)",
        },
    )
    assert rec.token_input is None
    assert rec.token_output == 21


def test_regex_with_cost_pattern() -> None:
    transcript = "IN=1000 OUT=500 COST=42"
    rec = parse_usage(
        transcript,
        {
            "kind": "regex",
            "input_pattern": r"IN=(\d+)",
            "output_pattern": r"OUT=(\d+)",
            "cost_pattern": r"COST=(\d+)",
        },
    )
    assert rec.cost_usd_micros == 42


# ── json_block ────────────────────────────────────────────────────────────


def test_json_block_picks_last_usage() -> None:
    transcript = (
        'preamble\n'
        '{"id": 1, "usage": {"input_tokens": 1, "output_tokens": 2}}\n'
        'middle stuff\n'
        '{"id": 2, "usage": {"input_tokens": 99, "output_tokens": 33}}\n'
        'epilog'
    )
    rec = parse_usage(transcript, {"kind": "json_block"})
    assert rec.token_input == 99
    assert rec.token_output == 33


def test_json_block_handles_nested_usage() -> None:
    transcript = (
        '{"meta": {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}}'
    )
    rec = parse_usage(transcript, {"kind": "json_block"})
    assert rec.token_input == 10
    assert rec.token_output == 5


def test_json_block_no_usage_returns_empty() -> None:
    rec = parse_usage('{"foo": "bar"}', {"kind": "json_block"})
    assert rec.is_empty()


# ── cost ──────────────────────────────────────────────────────────────────


def test_direct_cost_in_transcript() -> None:
    transcript = '{"input_tokens": 1, "output_tokens": 1, "cost_usd_micros": 12345}'
    rec = parse_usage(transcript, {"kind": "claude"})
    assert rec.cost_usd_micros == 12345


def test_cost_skipped_when_no_factors_or_pattern() -> None:
    transcript = '{"input_tokens": 100, "output_tokens": 50}'
    rec = parse_usage(transcript, {"kind": "claude"})
    assert rec.cost_usd_micros is None


def test_cost_only_input_factor() -> None:
    transcript = '{"input_tokens": 1000, "output_tokens": 500}'
    rec = parse_usage(
        transcript,
        {"kind": "claude", "cost_per_input_micros": 2_000_000},
    )
    assert rec.cost_usd_micros == 2000


def test_empty_transcript() -> None:
    assert parse_usage("", {"kind": "claude"}).is_empty()
    assert parse_usage("", {"kind": "openai"}).is_empty()
