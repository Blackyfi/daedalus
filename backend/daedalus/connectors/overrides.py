"""Resolve effective per-run settings, honouring connector-level overrides.

When a Connector has ``force_project_overrides=True``, every project that
uses it gets the connector's ``override_*`` values injected in place of its
own (model, wall-clock, argus_enabled, max_fix_loops). Each override is
independently nullable so operators can override only some fields and leave
the rest to the project. When ``force_project_overrides=False`` the
connector's overrides are ignored entirely — projects use their own values.

This module is the single source of truth for that merge so callers in the
runner, scheduler, planner, and Argus path stay consistent.
"""
from __future__ import annotations

from dataclasses import dataclass

from daedalus.db.models import Connector, Project


@dataclass(frozen=True)
class EffectiveSettings:
    planning_model: str | None
    task_model: str | None
    verifier_model: str | None
    wall_clock_minutes: int | None
    argus_enabled: bool
    max_fix_loops: int


def resolve(project: Project | None, connector: Connector | None) -> EffectiveSettings:
    """Merge a connector's overrides on top of project values.

    ``project`` may be None (e.g. when the run has been orphaned) — defaults
    are used in that case. ``connector`` may be None when the resolved
    connector is missing or no connector is in play; in that case project
    values pass through untouched.
    """
    planning = project.planning_model if project else None
    task = project.task_model if project else None
    verifier = project.verifier_model if project else None
    wall_clock = project.wall_clock_minutes_override if project else None
    argus_on = project.argus_enabled if project else True
    max_fix = project.max_fix_loops if project else 3

    if connector is not None and connector.force_project_overrides:
        if connector.override_planning_model is not None:
            planning = connector.override_planning_model
        if connector.override_task_model is not None:
            task = connector.override_task_model
        if connector.override_verifier_model is not None:
            verifier = connector.override_verifier_model
        if connector.override_wall_clock_minutes is not None:
            wall_clock = connector.override_wall_clock_minutes
        if connector.override_argus_enabled is not None:
            argus_on = connector.override_argus_enabled
        if connector.override_max_fix_loops is not None:
            max_fix = connector.override_max_fix_loops

    return EffectiveSettings(
        planning_model=planning,
        task_model=task,
        verifier_model=verifier,
        wall_clock_minutes=wall_clock,
        argus_enabled=argus_on,
        max_fix_loops=max_fix,
    )
