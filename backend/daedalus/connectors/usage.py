"""Per-connector token usage / cost parsers.

Connector specs may declare a ``usage_parser`` block that tells Talos how to
extract token counts from the agent's transcript. The parser produces three
optional integers — ``token_input``, ``token_output``, ``cost_usd_micros`` —
which are persisted onto the ``Run`` row.

Supported parser kinds:

* ``claude`` — matches the Anthropic CLI's reported usage lines and any JSON
  ``usage`` blocks the SDK emits. Recognises ``input_tokens`` /
  ``output_tokens`` keys and the human-readable ``X input, Y output`` form.

* ``openai`` — matches the Codex CLI, the OpenAI Python SDK, and any tool
  that emits ``prompt_tokens`` / ``completion_tokens`` (camelCase or
  snake_case).

* ``regex`` — generic. The connector supplies ``input_pattern`` /
  ``output_pattern``, each capturing one integer. Optional
  ``cost_pattern`` captures cost in USD micros directly.

* ``json_block`` — finds the *last* ``{"usage": {...}}`` JSON object in the
  transcript. Useful for connectors that emit a single end-of-run report.

If no parser is configured the run records ``None`` for all three columns.

Cost is computed from ``cost_per_input_micros`` and ``cost_per_output_micros``
(USD per **million** tokens, expressed in micros — i.e. 3_000_000 means $3.00
per 1M tokens). Either cost factor may be omitted; if both are missing the
``cost_usd_micros`` column stays ``None``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class UsageRecord:
    """Result of parsing a transcript for token usage."""

    token_input: int | None = None
    token_output: int | None = None
    cost_usd_micros: int | None = None

    def is_empty(self) -> bool:
        return (
            self.token_input is None
            and self.token_output is None
            and self.cost_usd_micros is None
        )


# ── public entry point ────────────────────────────────────────────────────


def parse_usage(transcript: str, parser_spec: dict[str, Any] | None) -> UsageRecord:
    """Parse a transcript using the connector's ``usage_parser`` spec.

    Returns an empty ``UsageRecord`` if the spec is missing, the kind is
    unknown, or no matches were found. Never raises — parser failures are
    logged at warn and swallowed so they don't poison run completion.
    """
    if not parser_spec or not isinstance(parser_spec, dict):
        return UsageRecord()

    kind = parser_spec.get("kind")
    try:
        if kind == "claude":
            tokens = _parse_claude(transcript)
        elif kind == "openai":
            tokens = _parse_openai(transcript)
        elif kind == "regex":
            tokens = _parse_regex(transcript, parser_spec)
        elif kind == "json_block":
            tokens = _parse_json_block(transcript)
        else:
            log.warning("usage_parser.unknown_kind", kind=kind)
            return UsageRecord()
    except Exception:
        log.warning("usage_parser.failed", kind=kind, exc_info=True)
        return UsageRecord()

    cost = _compute_cost(parser_spec, tokens, transcript)
    return UsageRecord(
        token_input=tokens[0],
        token_output=tokens[1],
        cost_usd_micros=cost,
    )


# ── parser implementations ────────────────────────────────────────────────


# Anthropic CLI emits things like:
#   tokens used: 1234 input, 567 output
#   "input_tokens": 1234, "output_tokens": 567
_CLAUDE_PROSE_RE = re.compile(
    r"(?:tokens?\s+used\s*:?\s*)?(\d[\d,]*)\s+input[^\d]+(\d[\d,]*)\s+output",
    re.IGNORECASE,
)
_CLAUDE_JSON_INPUT_RE = re.compile(r'"input_tokens"\s*:\s*(\d+)')
_CLAUDE_JSON_OUTPUT_RE = re.compile(r'"output_tokens"\s*:\s*(\d+)')


def _parse_claude(transcript: str) -> tuple[int | None, int | None]:
    text = _strip_ansi(transcript)
    inputs = [int(m.group(1)) for m in _CLAUDE_JSON_INPUT_RE.finditer(text)]
    outputs = [int(m.group(1)) for m in _CLAUDE_JSON_OUTPUT_RE.finditer(text)]
    if inputs or outputs:
        return (sum(inputs) or None, sum(outputs) or None)

    matches = list(_CLAUDE_PROSE_RE.finditer(text))
    if matches:
        last = matches[-1]
        return (
            int(last.group(1).replace(",", "")),
            int(last.group(2).replace(",", "")),
        )
    return (None, None)


_OPENAI_PROMPT_RE = re.compile(r'"?prompt[_-]?tokens"?\s*[:=]\s*(\d+)', re.IGNORECASE)
_OPENAI_COMPLETION_RE = re.compile(
    r'"?completion[_-]?tokens"?\s*[:=]\s*(\d+)', re.IGNORECASE
)


def _parse_openai(transcript: str) -> tuple[int | None, int | None]:
    text = _strip_ansi(transcript)
    inputs = [int(m.group(1)) for m in _OPENAI_PROMPT_RE.finditer(text)]
    outputs = [int(m.group(1)) for m in _OPENAI_COMPLETION_RE.finditer(text)]
    return (sum(inputs) or None, sum(outputs) or None)


def _parse_regex(
    transcript: str, parser_spec: dict[str, Any]
) -> tuple[int | None, int | None]:
    text = _strip_ansi(transcript)
    in_pat = parser_spec.get("input_pattern")
    out_pat = parser_spec.get("output_pattern")
    in_total: int | None = None
    out_total: int | None = None
    if in_pat:
        in_total = _sum_first_capture(text, in_pat)
    if out_pat:
        out_total = _sum_first_capture(text, out_pat)
    return (in_total, out_total)


def _parse_json_block(transcript: str) -> tuple[int | None, int | None]:
    text = _strip_ansi(transcript)
    # Walk the string, attempt to json-decode every '{' ... matching '}' window
    # and pick out the last object containing a "usage" key. We want the *last*
    # one because agents typically emit interim usage during streaming and a
    # final aggregate at the end.
    best: dict[str, Any] | None = None
    for start in _candidate_json_starts(text):
        obj = _decode_object_at(text, start)
        if obj is None:
            continue
        usage = _find_usage(obj)
        if usage is not None:
            best = usage
    if best is None:
        return (None, None)
    return (_extract_int(best, _USAGE_INPUT_KEYS), _extract_int(best, _USAGE_OUTPUT_KEYS))


# ── helpers ──────────────────────────────────────────────────────────────


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _sum_first_capture(text: str, pattern: str) -> int | None:
    try:
        compiled = re.compile(pattern)
    except re.error:
        log.warning("usage_parser.bad_pattern", pattern=pattern)
        return None
    total = 0
    found = False
    for match in compiled.finditer(text):
        if not match.groups():
            continue
        try:
            total += int(match.group(1).replace(",", ""))
            found = True
        except (ValueError, AttributeError):
            continue
    return total if found else None


def _candidate_json_starts(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch == "{"]


def _decode_object_at(text: str, start: int) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[start:])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


_USAGE_INPUT_KEYS = ("input_tokens", "prompt_tokens", "tokens_in", "input")
_USAGE_OUTPUT_KEYS = ("output_tokens", "completion_tokens", "tokens_out", "output")


def _find_usage(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if "usage" in obj and isinstance(obj["usage"], dict):
            return obj["usage"]
        for value in obj.values():
            found = _find_usage(value)
            if found is not None:
                return found
    return None


def _extract_int(obj: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


_COST_DIRECT_RE = re.compile(r'"cost_usd_micros"\s*:\s*(\d+)')


def _compute_cost(
    parser_spec: dict[str, Any],
    tokens: tuple[int | None, int | None],
    transcript: str,
) -> int | None:
    direct_pattern = parser_spec.get("cost_pattern")
    if direct_pattern:
        direct = _sum_first_capture(_strip_ansi(transcript), direct_pattern)
        if direct is not None:
            return direct

    direct = _sum_first_capture(_strip_ansi(transcript), _COST_DIRECT_RE.pattern)
    if direct is not None:
        return direct

    in_rate = parser_spec.get("cost_per_input_micros")
    out_rate = parser_spec.get("cost_per_output_micros")
    if in_rate is None and out_rate is None:
        return None

    total = 0
    counted = False
    if tokens[0] is not None and in_rate is not None:
        total += (tokens[0] * int(in_rate)) // 1_000_000
        counted = True
    if tokens[1] is not None and out_rate is not None:
        total += (tokens[1] * int(out_rate)) // 1_000_000
        counted = True
    return total if counted else None
