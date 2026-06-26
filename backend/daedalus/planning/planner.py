"""Planning — read project context (repo tree, README, existing tasks, ideas)
and ask the LLM to propose a structured task list with rationale.

Falls back to the deterministic 1-idea→1-task transform if the LLM is
unreachable, so the platform keeps working in dev environments without an
inference server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from daedalus.llm import LLMError, get_llm_client
from daedalus.llm.client import ChatMessage

log = structlog.get_logger()


@dataclass
class TaskProposal:
    title: str
    description: str = ""
    acceptance_criteria: str = ""
    priority: str = "P2"
    suggested_connector: str | None = None
    depends_on: list[int] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_idea_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "priority": self.priority,
            "suggested_connector": self.suggested_connector,
            "depends_on": self.depends_on,
            "tags": list(dict.fromkeys([*self.tags, "planned-from-idea"])),
            "source_idea_id": self.source_idea_id,
        }


@dataclass
class Proposal:
    proposed_tasks: list[TaskProposal]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_tasks": [t.to_dict() for t in self.proposed_tasks],
            "rationale": self.rationale,
        }


_SYSTEM_PROMPT = """\
You are the Daedalus planning agent. You are given a software project's
context (repo tree summary, README excerpt, current task list) and a list of
loose ideas. Decompose the ideas into a concrete, dependency-ordered task
list that another agent can execute.

Reply with a single JSON object — no prose, no code fences — of this exact
shape:

{
  "proposed_tasks": [
    {
      "title": "<= 80 chars, imperative",
      "description": "what to do, in markdown",
      "acceptance_criteria": "what 'done' looks like",
      "priority": "P0" | "P1" | "P2" | "P3",
      "suggested_connector": "<connector_id>" | null,
      "depends_on": [<index of earlier task in this list>, ...],
      "tags": [...],
      "source_idea_id": "<idea uuid or null>"
    }
  ],
  "rationale": "<= 600 chars explaining the decomposition"
}

Rules:
- Group related ideas into one task; split big ideas into several.
- Use `depends_on` indexes (zero-based) to capture order, never IDs.
- Default priority is P2; P0/P1 only when blocking.
- If the project already has a default connector and an idea doesn't override
  it, leave `suggested_connector` null.
"""


async def build_proposal(
    *,
    project_name: str,
    project_description: str,
    workspace_path: str,
    git_default_branch: str,
    default_connector_id: str | None,
    available_connector_ids: list[str],
    existing_tasks: list[dict[str, Any]],
    ideas: list[dict[str, Any]],
    planning_model: str | None = None,
) -> Proposal:
    """Build a Proposal from project context + ideas. Pure async, no DB access."""
    if not ideas:
        return Proposal(proposed_tasks=[], rationale="No ideas in the box.")

    repo_tree = _summarise_repo_tree(workspace_path, max_lines=200)
    readme_excerpt = _read_readme(workspace_path, max_chars=4000)
    tasks_summary = _summarise_tasks(existing_tasks, max_count=40)
    ideas_block = _format_ideas(ideas)

    user_prompt = f"""\
Project: {project_name}
Description: {project_description or '(none)'}
Default branch: {git_default_branch}
Default connector: {default_connector_id or '(none)'}
Available connectors: {', '.join(available_connector_ids) or '(none)'}

--- Repo tree summary ---
{repo_tree or '(empty workspace)'}
--- end ---

--- README excerpt ---
{readme_excerpt or '(no README)'}
--- end ---

--- Existing tasks (latest {min(40, len(existing_tasks))}) ---
{tasks_summary or '(none)'}
--- end ---

--- Ideas to plan ---
{ideas_block}
--- end ---

Reply with the JSON proposal now.
"""

    client = get_llm_client()
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]

    try:
        data = await client.chat_json(
            messages, temperature=0.2, max_tokens=3072, model=planning_model
        )
    except LLMError as exc:
        log.warning("planning.llm_unavailable", error=str(exc))
        return _fallback_proposal(ideas, default_connector_id, str(exc))

    return _parse_proposal(data, ideas, default_connector_id)


def _parse_proposal(
    data: Any, ideas: list[dict[str, Any]], default_connector_id: str | None
) -> Proposal:
    if not isinstance(data, dict):
        return _fallback_proposal(ideas, default_connector_id, "LLM returned non-object")

    raw_tasks = data.get("proposed_tasks") or []
    parsed: list[TaskProposal] = []
    if isinstance(raw_tasks, list):
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                continue
            depends_raw = raw.get("depends_on") or []
            depends = [int(d) for d in depends_raw if isinstance(d, (int, float))]
            parsed.append(
                TaskProposal(
                    title=str(raw.get("title") or "Untitled task")[:240],
                    description=str(raw.get("description") or ""),
                    acceptance_criteria=str(raw.get("acceptance_criteria") or "Deliver the requested change."),
                    priority=str(raw.get("priority") or "P2"),
                    suggested_connector=raw.get("suggested_connector") or None,
                    depends_on=depends,
                    tags=[str(t) for t in (raw.get("tags") or []) if isinstance(t, str)],
                    source_idea_id=str(raw["source_idea_id"]) if raw.get("source_idea_id") else None,
                )
            )

    if not parsed:
        return _fallback_proposal(ideas, default_connector_id, "LLM returned no tasks")

    rationale = str(data.get("rationale") or "").strip() or f"LLM drafted {len(parsed)} task(s) from {len(ideas)} idea(s)."
    return Proposal(proposed_tasks=parsed, rationale=rationale)


def _fallback_proposal(
    ideas: list[dict[str, Any]], default_connector_id: str | None, reason: str
) -> Proposal:
    proposals: list[TaskProposal] = []
    for idea in ideas:
        title, description, acceptance = _idea_to_task_fields(str(idea.get("text") or ""))
        proposals.append(
            TaskProposal(
                title=title,
                description=description,
                acceptance_criteria=acceptance,
                priority="P2",
                suggested_connector=default_connector_id,
                tags=list(dict.fromkeys([*(idea.get("tags") or []), "planned-from-idea"])),
                source_idea_id=str(idea.get("id")) if idea.get("id") else None,
            )
        )
    rationale = (
        f"LLM unavailable ({reason}); drafted {len(proposals)} deterministic task(s) "
        f"from the idea box. Edit titles, criteria, and connector before confirming."
    )
    return Proposal(proposed_tasks=proposals, rationale=rationale)


def _idea_to_task_fields(text: str) -> tuple[str, str, str]:
    """Kept identical to api.routes.internal so existing tests pass."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ("Untitled task", "", "Deliver the requested change.")

    title = lines[0][:240]
    description_lines: list[str] = []
    acceptance_lines: list[str] = []
    target = description_lines
    for line in lines[1:]:
        if line.lower().startswith("acceptance:"):
            target = acceptance_lines
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                target.append(remainder)
            continue
        target.append(line)

    description = "\n".join(description_lines).strip()
    acceptance = (
        "\n".join(acceptance_lines).strip()
        or "Deliver the requested change and keep the workspace reviewable."
    )
    return (title, description, acceptance)


def _summarise_repo_tree(workspace_path: str, *, max_lines: int) -> str:
    if not workspace_path or not os.path.isdir(workspace_path):
        return ""
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".cache"}
    lines: list[str] = []
    base = Path(workspace_path)
    try:
        for path in sorted(base.rglob("*")):
            try:
                rel = path.relative_to(base)
            except ValueError:
                continue
            parts = rel.parts
            if any(part in skip for part in parts):
                continue
            depth = len(parts) - 1
            if depth > 4:
                continue
            indent = "  " * depth
            kind = "/" if path.is_dir() else ""
            lines.append(f"{indent}{rel.name}{kind}")
            if len(lines) >= max_lines:
                lines.append("... (truncated)")
                break
    except Exception:
        return ""
    return "\n".join(lines)


def _read_readme(workspace_path: str, *, max_chars: int) -> str:
    if not workspace_path:
        return ""
    base = Path(workspace_path)
    for name in ("README.md", "README.rst", "README.txt", "README"):
        candidate = base / name
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            return text[:max_chars]
    return ""


def _summarise_tasks(tasks: list[dict[str, Any]], *, max_count: int) -> str:
    if not tasks:
        return ""
    lines: list[str] = []
    for task in tasks[:max_count]:
        lines.append(f"- [{task.get('status')}] ({task.get('priority')}) {task.get('title')}")
    return "\n".join(lines)


def _format_ideas(ideas: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for idea in ideas:
        idea_id = idea.get("id") or ""
        text = (idea.get("text") or "").strip()
        tags = ", ".join(idea.get("tags") or [])
        tag_suffix = f" [tags: {tags}]" if tags else ""
        out.append(f"- id={idea_id}{tag_suffix}\n  {text}")
    return "\n".join(out)


# Re-export for tests that imported the old internal-api implementation directly.
__all__ = ["Proposal", "TaskProposal", "_idea_to_task_fields", "build_proposal"]
