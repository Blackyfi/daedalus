"""Argus — read git diff + verification command output, ask the LLM for a
structured pass/partial/fail verdict, return findings.

Used by `hermes.scheduler` after a verification run completes. The actual
shell `verify_commands` are still run by Talos (Hephaestus role); this module
*judges* the result.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

import structlog

from daedalus.core.settings import get_settings
from daedalus.llm import LLMError, get_llm_client
from daedalus.llm.client import ChatMessage

log = structlog.get_logger()


class WorktreeUnreadableError(Exception):
    """Raised when the verifier process can't read a run worktree.

    This is an *infrastructure* problem (e.g., the workspaces volume isn't
    mounted into the container running this code, or the worktree was deleted
    out from under us). It is NOT a verdict on the agent's work — callers
    must treat it as "retry once the operator has fixed the infra", never as
    a fail/partial verdict against the task. We had a long stretch where this
    failure mode was silently swallowed and every code-change task was
    falsely marked needs_fixes; the typed exception is here so the silence
    can never come back.
    """


@dataclass
class ArgusVerdict:
    verdict: str  # "pass" | "partial" | "fail"
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    suggested_fix_task: dict[str, Any] | None = None
    raw_response: str | None = None


_SYSTEM_PROMPT = """\
You are Argus, an automated verifier for an autonomous coding agent platform.
Your job is to decide whether a task was actually completed by inspecting (a)
the git diff produced, (b) the output of verification commands, and — when
present — (c) the agent's own final report from its run transcript.

You always reply with a single JSON object — no prose, no code fences — of
this exact shape:

{
  "verdict": "pass" | "partial" | "fail",
  "summary": "<= 280 chars",
  "findings": [
    {
      "severity": "blocker" | "major" | "minor",
      "category": "bug" | "missing" | "regression" | "test" | "style",
      "description": "...",
      "evidence": "exact log line or diff hunk reference"
    }
  ],
  "suggested_fix_task": {
    "title": "...",
    "description": "...",
    "acceptance_criteria": "..."
  } | null
}

Rules:
- Judge each acceptance criterion separately and ground every finding in
  EXACT evidence: quote the diff hunk, the failing-test line, or the verify
  output line it refers to. A finding with no concrete evidence is not valid.
- Do NOT return "pass" unless you can point to evidence that each criterion is
  met. When in doubt, return "partial", not "pass".
- Treat deleting, skipping, or weakening tests as a blocker, never a pass —
  a green verify obtained by removing its own tests is not a real pass.
- "pass" if every acceptance criterion is satisfied AND every verify command
  exited 0. Code-change tasks must also show a diff consistent with the task.
- For analytical tasks (review / audit / static analysis / "verify that…"
  with no required code change), an empty diff is acceptable WHEN the agent's
  final report substantively addresses every acceptance criterion with
  concrete evidence (file paths, line numbers, command output, decisions).
  In that case the report itself is the deliverable.
- "fail" if no meaningful work was done OR a verify command failed in a way
  that blocks all acceptance criteria OR (for an analytical task) the report
  is missing, generic, or skips acceptance criteria.
- "partial" otherwise.
- "suggested_fix_task" must be null when verdict is "pass".
"""


async def verify_run(
    *,
    task_title: str,
    task_description: str,
    acceptance_criteria: str,
    verify_commands: list[str],
    diff_text: str,
    verify_output: str,
    verify_exit_code: int | None,
    verifier_model: str | None = None,
    agent_final_text: str = "",
) -> ArgusVerdict:
    """Ask the LLM to verify a task. Falls back to a deterministic verdict
    on LLM error so the platform keeps making progress.

    `agent_final_text` is the agent's own final report extracted from its
    run transcript; supplied by the scheduler when the diff is empty so the
    verifier can judge analytical (review/audit) tasks fairly.
    """
    settings = get_settings()
    diff_excerpt = _truncate(diff_text, settings.llm_max_diff_chars)
    output_excerpt = _truncate(verify_output, settings.llm_max_log_chars)
    report_excerpt = _truncate(agent_final_text, settings.llm_max_log_chars)
    verify_block = "\n".join(f"  $ {cmd}" for cmd in verify_commands) or "  (none configured)"

    report_section = (
        f"--- Agent's final report from transcript (truncated) ---\n"
        f"{report_excerpt}\n"
        f"--- end report ---\n\n"
    ) if report_excerpt else ""

    user_prompt = f"""\
Task title: {task_title}

Task description:
{task_description or '(none)'}

Acceptance criteria:
{acceptance_criteria or '(none)'}

Verify commands run:
{verify_block}

Verify command exit code: {verify_exit_code if verify_exit_code is not None else 'unknown'}

--- Verify command output (truncated) ---
{output_excerpt or '(no output captured)'}
--- end output ---

--- Git diff vs default branch (truncated) ---
{diff_excerpt or '(no diff)'}
--- end diff ---

{report_section}Reply with the JSON verdict now.
"""

    client = get_llm_client()
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]

    try:
        data = await client.chat_json(
            messages,
            model=verifier_model or client.verifier_model,
            temperature=0.1,
        )
    except LLMError as exc:
        log.warning("argus.llm_unavailable", error=str(exc))
        return _fallback_verdict(verify_exit_code, str(exc))

    verdict = (data.get("verdict") or "fail").lower()
    if verdict not in {"pass", "partial", "fail"}:
        verdict = "fail"

    findings_raw = data.get("findings") or []
    findings: list[dict[str, Any]] = []
    if isinstance(findings_raw, list):
        for f in findings_raw:
            if not isinstance(f, dict):
                continue
            findings.append(
                {
                    "severity": str(f.get("severity") or "minor"),
                    "category": str(f.get("category") or "bug"),
                    "description": str(f.get("description") or "").strip(),
                    "evidence": str(f.get("evidence") or "").strip(),
                }
            )

    suggested = data.get("suggested_fix_task")
    summary = str(data.get("summary") or "").strip()

    # Deterministic tamper gate: if the diff shows tests deleted/skipped/
    # weakened, the verdict can never be "pass", whatever the LLM said.
    tamper = detect_tampering(diff_text)
    if tamper:
        findings = tamper + findings
        if verdict == "pass":
            verdict = "fail"
            summary = (
                "Blocked by tamper gate: tests were deleted/skipped/weakened, so a "
                "green verify is not trustworthy. " + summary
            ).strip()
        suggested = suggested or {
            "title": "Restore tests removed/weakened during the fix",
            "description": (
                "Argus detected test tampering (deleted, skipped, or assertion-"
                "stripped tests). Reinstate the tests and make the code pass them."
            ),
            "acceptance_criteria": "No tests are deleted, skipped, or weakened; verify commands pass.",
        }

    if verdict == "pass":
        suggested = None
    if suggested is not None and not isinstance(suggested, dict):
        suggested = None

    return ArgusVerdict(
        verdict=verdict,
        summary=summary,
        findings=findings,
        suggested_fix_task=suggested,
    )


# --- deterministic tamper / "fake-green" detection -----------------------
#
# Frontier coding agents sometimes make `verify_commands` pass by deleting,
# skipping, or weakening tests instead of fixing the code (reward hacking).
# These signals are HIGH-CONFIDENCE and force a non-pass verdict regardless of
# what the LLM judge says — the cheap deterministic gate in front of the
# (expensive, fallible) LLM. Kept conservative to avoid blocking honest work.

_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:test_[^/]+\.py"
    r"|[^/]+_test\.(?:py|go)"
    r"|[^/]+\.(?:test|spec)\.(?:ts|tsx|js|jsx|mjs|cjs))$"
)
_ASSERT_RE = re.compile(
    r"\b(?:assert|expect\(|\.to(?:Be|Equal|Throw|Match|Contain)"
    r"|EXPECT_[A-Z]+|ASSERT_[A-Z]+|self\.assert)"
)
_SKIP_RE = re.compile(
    r"@(?:pytest\.mark\.|unittest\.)?skip"
    r"|pytest\.skip\("
    r"|\b(?:it|describe|test)\.skip\("
    r"|\bx(?:it|describe)\("
    r"|\bt\.Skip\("
    r"|@Disabled\b"
)


def _is_test_path(path: str) -> bool:
    return bool(path) and bool(_TEST_PATH_RE.search(path))


def detect_tampering(diff_text: str) -> list[dict[str, Any]]:
    """Scan a unified git diff for test-tampering and return blocker findings.

    Detects, conservatively:
      1. a whole test file deleted,
      2. skip markers added to a test file,
      3. assertions net-removed from a test file (removed with no replacement).
    Empty list means "no tampering observed" — never a pass/fail by itself.
    """
    findings: list[dict[str, Any]] = []
    cur_path = ""
    is_test = False
    deleted_file = False
    removed_asserts = 0
    added_asserts = 0

    def _flush() -> None:
        nonlocal removed_asserts, added_asserts
        if is_test and cur_path and deleted_file:
            findings.append(
                {
                    "severity": "blocker",
                    "category": "test",
                    "description": (
                        f"Test file `{cur_path}` was deleted — verify commands may "
                        f"pass only because their tests no longer exist."
                    ),
                    "evidence": f"deleted file: {cur_path}",
                }
            )
        elif is_test and removed_asserts > 0 and added_asserts == 0:
            findings.append(
                {
                    "severity": "blocker",
                    "category": "test",
                    "description": (
                        f"{removed_asserts} assertion(s) removed from test file "
                        f"`{cur_path}` with no replacement — possible fake-green."
                    ),
                    "evidence": f"net -{removed_asserts} assertions in {cur_path}",
                }
            )
        removed_asserts = 0
        added_asserts = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            _flush()
            cur_path = ""
            is_test = False
            deleted_file = False
            continue
        if raw.startswith("deleted file mode"):
            deleted_file = True
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            if path and path != "/dev/null":
                cur_path = path
                is_test = _is_test_path(path)
            continue
        if raw.startswith("--- "):
            # For deletions, +++ is /dev/null, so take the path from ---.
            path = raw[4:].strip()
            path = path[2:] if path.startswith("a/") else path
            if path and path != "/dev/null" and not cur_path:
                cur_path = path
                is_test = _is_test_path(path)
            continue
        if raw.startswith("@@") or raw.startswith("index ") or raw.startswith("new file"):
            continue
        if not is_test:
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            if _ASSERT_RE.search(raw):
                removed_asserts += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            if _ASSERT_RE.search(raw):
                added_asserts += 1
            if _SKIP_RE.search(raw):
                findings.append(
                    {
                        "severity": "blocker",
                        "category": "test",
                        "description": (
                            f"Test skip/disable added in `{cur_path}` — verification "
                            f"may pass only because a test was silenced."
                        ),
                        "evidence": raw.strip()[:200],
                    }
                )

    _flush()
    return findings


_TRANSIENT_RE = re.compile(
    r"connection reset|connection refused|temporarily unavailable|timed out|timeout"
    r"|ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|network is unreachable"
    r"|could not resolve host|503 service unavailable|429 too many requests"
    r"|rate limit|deadline exceeded|broken pipe",
    re.IGNORECASE,
)


def is_transient_failure(verify_output: str) -> bool:
    """Heuristic: did verification fail for an infra/transient reason (network,
    timeout, rate-limit) rather than the agent's code (#24)? Callers can retry
    a verify once on transient failure before declaring needs_fixes."""
    return bool(verify_output) and bool(_TRANSIENT_RE.search(verify_output))


def extract_agent_final_text(transcript: str) -> str:
    """Pull the agent's final summary out of a Claude Code stream-json
    transcript. The CLI writes one JSON object per line; the last one with
    type=result carries the cleaned final assistant text. Falls back to
    concatenating any `text` content blocks if no result envelope is found.

    Returns an empty string if the transcript is empty, non-JSON, or carries
    no usable text — callers should treat that as "no report available".
    """
    if not transcript:
        return ""
    last_result_text = ""
    text_blocks: list[str] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = obj.get("type")
        if t == "result":
            r = obj.get("result")
            if isinstance(r, str) and r.strip():
                last_result_text = r
        elif t == "assistant":
            msg = obj.get("message") or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    txt = block.get("text") or ""
                    if isinstance(txt, str) and txt.strip():
                        text_blocks.append(txt)
    if last_result_text:
        return last_result_text
    return "\n\n".join(text_blocks[-3:])  # last few text blocks at most


async def collect_diff(worktree_path: str, default_branch: str) -> str:
    """`git diff <default_branch>...HEAD` from the run's worktree.

    Raises WorktreeUnreadableError when the path doesn't exist or isn't a git
    worktree from this process's perspective. This separates "the agent
    produced no diff" (return "") from "this process can't see the
    workspace" (raise) — the latter must NEVER be reported to the LLM
    verifier as an empty diff, because that's the behaviour that produced
    the long phantom-commit-fail cascade.
    """
    if not worktree_path:
        return ""
    if not os.path.isdir(worktree_path):
        raise WorktreeUnreadableError(f"worktree path missing: {worktree_path}")
    try:
        probe = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--is-inside-work-tree",
            cwd=worktree_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, perr = await probe.communicate()
        if probe.returncode != 0:
            raise WorktreeUnreadableError(
                f"not a git worktree at {worktree_path}: "
                f"{perr.decode(errors='replace').strip()[:200]}"
            )
    except FileNotFoundError as exc:
        raise WorktreeUnreadableError(f"cannot exec git: {exc}") from exc
    # Exclude common compiled / vendored / cache artefacts via git pathspecs.
    # A diff that contains only e.g. .pyc files is functionally empty work —
    # we want Argus to grade the agent on real code, not bytecode noise. See
    # task 5256b444 in the needs_fixes audit.
    diff_excludes = [
        ":(exclude,glob)**/__pycache__/**",
        ":(exclude,glob)**/*.pyc",
        ":(exclude,glob)**/*.pyo",
        ":(exclude,glob)**/node_modules/**",
        ":(exclude,glob)**/.git/**",
        ":(exclude,glob)**/*.class",
    ]
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", f"{default_branch}...HEAD", "--no-color", "--",
        ".",
        *diff_excludes,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if proc.returncode not in (0, None):
        return ""
    return out.decode(errors="replace")


async def run_verify_commands(worktree_path: str, commands: list[str]) -> tuple[int, str]:
    """Run the connector's `verify_commands` joined as one bash -lc script.
    Returns (exit_code, captured_combined_output)."""
    if not commands:
        return (0, "")
    script = "set -e\n" + "\n".join(commands)
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", script,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return (proc.returncode if proc.returncode is not None else 1, out.decode(errors="replace"))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... [{len(text) - limit} chars elided] ...\n\n{tail}"


def _fallback_verdict(verify_exit_code: int | None, reason: str) -> ArgusVerdict:
    if verify_exit_code in (0, None):
        return ArgusVerdict(
            verdict="pass",
            summary=f"LLM verifier unavailable; verify commands exited cleanly. ({reason})",
        )
    return ArgusVerdict(
        verdict="fail",
        summary=f"LLM verifier unavailable; verify commands failed (exit {verify_exit_code}). ({reason})",
        findings=[
            {
                "severity": "blocker",
                "category": "test",
                "description": "Verify commands failed and the LLM verifier was unreachable.",
                "evidence": shlex.quote(reason)[:500],
            }
        ],
        suggested_fix_task={
            "title": "Fix failing verify commands",
            "description": "Verification did not pass; the LLM verifier was unavailable so no findings detail is attached.",
            "acceptance_criteria": "All connector verify_commands must exit 0.",
        },
    )
