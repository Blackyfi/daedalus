"""Argus — read git diff + verification command output, ask the LLM for a
structured pass/partial/fail verdict, return findings.

Used by `hermes.scheduler` after a verification run completes. The actual
shell `verify_commands` are still run by Talos (Hephaestus role); this module
*judges* the result.
"""
from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field
from typing import Any

import structlog

from daedalus.core.settings import get_settings
from daedalus.llm import LLMError, get_llm_client
from daedalus.llm.client import ChatMessage

log = structlog.get_logger()


@dataclass
class ArgusVerdict:
    verdict: str  # "pass" | "partial" | "fail"
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    suggested_fix_task: dict[str, Any] | None = None
    raw_response: str | None = None


_SYSTEM_PROMPT = """\
You are Argus, an automated verifier for an autonomous coding agent platform.
Your job is to decide whether a task was actually completed by inspecting the
git diff produced and the output of verification commands (tests, linters,
builds).

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
- "pass" only if every acceptance criterion is satisfied AND every verify
  command exited 0 AND the diff is consistent with the task.
- "fail" if no meaningful work was done OR a verify command failed in a way
  that blocks all acceptance criteria.
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
) -> ArgusVerdict:
    """Ask the LLM to verify a task. Falls back to a deterministic verdict
    on LLM error so the platform keeps making progress."""
    settings = get_settings()
    diff_excerpt = _truncate(diff_text, settings.llm_max_diff_chars)
    output_excerpt = _truncate(verify_output, settings.llm_max_log_chars)
    verify_block = "\n".join(f"  $ {cmd}" for cmd in verify_commands) or "  (none configured)"

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

Reply with the JSON verdict now.
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
    if verdict == "pass":
        suggested = None
    if suggested is not None and not isinstance(suggested, dict):
        suggested = None

    return ArgusVerdict(
        verdict=verdict,
        summary=str(data.get("summary") or "").strip(),
        findings=findings,
        suggested_fix_task=suggested,
    )


async def collect_diff(worktree_path: str, default_branch: str) -> str:
    """`git diff <default_branch>...HEAD` from the run's worktree."""
    if not worktree_path:
        return ""
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", f"{default_branch}...HEAD", "--no-color",
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
