"""Internal automation routes used by background workers."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Connector, Idea, PlanProposal, PlanProposalStatus, Project, Task
from daedalus.planning import build_proposal
from daedalus.planning.planner import _idea_to_task_fields  # re-exported for compat tests

router = APIRouter()


def require_internal_key(x_daedalus_internal_key: Annotated[str | None, Header()] = None) -> None:
    if x_daedalus_internal_key != get_settings().session_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal key")


@router.post("/planning/generate", dependencies=[Depends(require_internal_key)])
async def generate_plan(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_session),
):
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "project_id is required")

    try:
        pid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid project_id") from exc

    project = await db.get(Project, pid)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    ideas_res = await db.execute(
        select(Idea)
        .where(Idea.project_id == pid, Idea.archived.is_(False))
        .order_by(Idea.sort_index.asc(), Idea.created_at.asc())
    )
    ideas = ideas_res.scalars().all()
    ideas_payload = [
        {"id": str(idea.id), "text": idea.text, "tags": list(idea.tags or [])}
        for idea in ideas
    ]

    tasks_res = await db.execute(
        select(Task).where(Task.project_id == pid).order_by(Task.created_at.desc()).limit(40)
    )
    existing_tasks = [
        {
            "id": str(task.id),
            "title": task.title,
            "status": task.status.value,
            "priority": task.priority.value,
        }
        for task in tasks_res.scalars().all()
    ]

    connectors_res = await db.execute(
        select(Connector.connector_id).where(Connector.enabled.is_(True))
    )
    available_connector_ids = [row[0] for row in connectors_res.all()]

    proposal = await build_proposal(
        project_name=project.name,
        project_description=project.description or "",
        workspace_path=project.workspace_path,
        git_default_branch=project.git_default_branch,
        default_connector_id=project.default_connector_id,
        available_connector_ids=available_connector_ids,
        existing_tasks=existing_tasks,
        ideas=ideas_payload,
        planning_model=project.planning_model,
    )

    run_id_raw = body.get("run_id")
    run_id: uuid.UUID | None = None
    if run_id_raw:
        try:
            run_id = uuid.UUID(run_id_raw)
        except ValueError:
            run_id = None

    record = PlanProposal(
        project_id=pid,
        run_id=run_id,
        status=PlanProposalStatus.pending,
        proposed_tasks=[t.to_dict() for t in proposal.proposed_tasks],
        rationale=proposal.rationale,
        source_idea_ids=[idea.id for idea in ideas],
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "status": "ok",
        "plan_id": str(record.id),
        "proposed_count": len(proposal.proposed_tasks),
    }


# Kept exported so tests/test_internal_planning.py keeps importing the old name.
__all__ = ["router", "_idea_to_task_fields", "require_internal_key"]
