"""Coverage for /api/v1/autorun routes — schema, quiet-hours math, role gate,
auto-trigger detection, and PATCH cross-field validation."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


# ---------- pure helper coverage --------------------------------------------

def test_in_quiet_hours_disabled_when_either_bound_null() -> None:
    from daedalus.api.routes.autorun import _in_quiet_hours

    assert _in_quiet_hours(None, 6) is False
    assert _in_quiet_hours(22, None) is False
    assert _in_quiet_hours(None, None) is False


def test_in_quiet_hours_disabled_when_bounds_equal() -> None:
    from daedalus.api.routes.autorun import _in_quiet_hours

    # An empty window (start == end) is treated as "disabled" — we never
    # want to silently mute auto-run for 24 hours just because someone set
    # both fields to the same hour.
    assert _in_quiet_hours(3, 3) is False


def test_in_quiet_hours_simple_window() -> None:
    from daedalus.api.routes.autorun import _in_quiet_hours

    now = datetime(2026, 5, 7, 4, 0, tzinfo=timezone.utc)
    assert _in_quiet_hours(2, 6, now=now) is True
    # boundary: end is exclusive
    assert _in_quiet_hours(2, 4, now=now) is False
    # outside window
    assert _in_quiet_hours(10, 14, now=now) is False


def test_in_quiet_hours_wraparound() -> None:
    from daedalus.api.routes.autorun import _in_quiet_hours

    # 22:00–06:00 wrap-around: matches 23:30 and 02:30 but not 12:00.
    night = datetime(2026, 5, 7, 23, 30, tzinfo=timezone.utc)
    morning = datetime(2026, 5, 7, 2, 30, tzinfo=timezone.utc)
    midday = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    assert _in_quiet_hours(22, 6, now=night) is True
    assert _in_quiet_hours(22, 6, now=morning) is True
    assert _in_quiet_hours(22, 6, now=midday) is False


def test_is_auto_triggered_requires_fix_loop_tag() -> None:
    from daedalus.api.routes.autorun import _is_auto_triggered

    auto_task = SimpleNamespace(tags=["urgent", "fix-loop"])
    manual_task = SimpleNamespace(tags=["urgent"])
    fake_run = SimpleNamespace()
    assert _is_auto_triggered(fake_run, auto_task) is True
    assert _is_auto_triggered(fake_run, manual_task) is False
    assert _is_auto_triggered(fake_run, None) is False


# ---------- schema coverage -------------------------------------------------

def test_autorun_config_patch_round_trip() -> None:
    from daedalus.api.schemas import AutoRunConfigPatch

    patch = AutoRunConfigPatch.model_validate(
        {
            "auto_run_fix": True,
            "max_fix_loops": 5,
            "wall_clock_minutes_override": 30,
            "default_connector_id": "claude",
            "auto_run_quiet_hours_start": 22,
            "auto_run_quiet_hours_end": 6,
            "auto_run_daily_cap": 12,
        }
    )
    assert patch.auto_run_fix is True
    assert patch.max_fix_loops == 5
    assert patch.auto_run_quiet_hours_start == 22
    assert patch.auto_run_quiet_hours_end == 6
    assert patch.auto_run_daily_cap == 12


def test_autorun_config_patch_rejects_out_of_range_hours() -> None:
    from daedalus.api.schemas import AutoRunConfigPatch

    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"auto_run_quiet_hours_start": 24})
    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"auto_run_quiet_hours_end": -1})


def test_autorun_config_patch_rejects_negative_caps() -> None:
    from daedalus.api.schemas import AutoRunConfigPatch

    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"auto_run_daily_cap": -1})
    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"max_fix_loops": -1})


def test_project_in_round_trips_new_fields() -> None:
    """The auto-run quiet-hours and daily-cap fields are reachable through the
    standard ProjectIn surface so client SDKs / OpenAPI consumers see them."""
    from daedalus.api.schemas import ProjectIn

    payload = ProjectIn.model_validate(
        {
            "name": "p",
            "workspace_path": "/tmp/p",
            "auto_run_quiet_hours_start": 1,
            "auto_run_quiet_hours_end": 5,
            "auto_run_daily_cap": 3,
        }
    )
    assert payload.auto_run_quiet_hours_start == 1
    assert payload.auto_run_quiet_hours_end == 5
    assert payload.auto_run_daily_cap == 3


# ---------- route handler coverage ------------------------------------------

class _FakeRunsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal AsyncSession fake — same shape as the one in test_ideas_patch.

    For autorun we need ``get(Project, pid)`` to resolve the project, plus
    ``execute(...)`` to return the run/task pairs the route walks.
    """

    def __init__(self, *, project, run_rows=None):
        self._project = project
        self._run_rows = run_rows or []
        self.added: list = []
        self.commit_count = 0
        self.flush_count = 0
        self.refresh_count = 0

    async def get(self, model, pid):
        if getattr(model, "__name__", "") == "Project" and pid == self._project.id:
            return self._project
        return None

    async def execute(self, _stmt):
        return _FakeRunsResult(self._run_rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1

    async def refresh(self, _obj) -> None:
        self.refresh_count += 1


def _make_user(role_value: str = "member"):
    from daedalus.db.models import Role

    return SimpleNamespace(id=uuid.uuid4(), role=Role(role_value))


def _make_project(*, owner_id, **overrides):
    from daedalus.db.models import Project

    proj = Project(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="proj",
        workspace_path="/tmp/proj",
    )
    # Set defaults explicitly: SQLAlchemy `default=` only fires at INSERT,
    # so an in-memory instance has them as `None` until we backfill.
    proj.auto_run_fix = overrides.get("auto_run_fix", False)
    proj.max_fix_loops = overrides.get("max_fix_loops", 3)
    proj.wall_clock_minutes_override = overrides.get("wall_clock_minutes_override")
    proj.default_connector_id = overrides.get("default_connector_id")
    proj.auto_run_quiet_hours_start = overrides.get("auto_run_quiet_hours_start")
    proj.auto_run_quiet_hours_end = overrides.get("auto_run_quiet_hours_end")
    proj.auto_run_daily_cap = overrides.get("auto_run_daily_cap", 0)
    proj.auto_run_concurrency_cap = overrides.get("auto_run_concurrency_cap", 1)
    proj.auto_run_hourly_cap = overrides.get("auto_run_hourly_cap", 0)
    proj.auto_run_allowed_connectors = overrides.get(
        "auto_run_allowed_connectors", []
    )
    proj.auto_run_eligible_statuses = overrides.get(
        "auto_run_eligible_statuses", ["backlog", "ready", "needs_fixes"]
    )
    return proj


def _fake_request():
    return SimpleNamespace(state=SimpleNamespace(cert_fp="fp:test"))


def _make_run(*, project_id, task=None, created_at=None):
    from daedalus.db.models import Run, RunKind, RunState

    run = Run(
        id=uuid.uuid4(),
        project_id=project_id,
        task_id=task.id if task is not None else None,
        kind=RunKind.task,
        state=RunState.completed,
    )
    run.created_at = created_at or datetime.now(timezone.utc)
    run.started_at = run.created_at
    run.finished_at = run.created_at
    return run


def _make_task(*, project_id, tags=None, title="t"):
    from daedalus.db.models import Task

    task = Task(
        id=uuid.uuid4(),
        project_id=project_id,
        title=title,
        tags=tags or [],
    )
    return task


@pytest.mark.asyncio
async def test_get_autorun_returns_status_and_eligible_statuses() -> None:
    from daedalus.api.routes.autorun import get_autorun

    user = _make_user()
    proj = _make_project(
        owner_id=user.id,
        auto_run_fix=True,
        max_fix_loops=5,
        auto_run_daily_cap=10,
    )
    auto_task = _make_task(project_id=proj.id, tags=["fix-loop"], title="auto")
    manual_task = _make_task(project_id=proj.id, tags=[], title="manual")
    db = _FakeSession(
        project=proj,
        run_rows=[
            (_make_run(project_id=proj.id, task=auto_task), auto_task),
            (_make_run(project_id=proj.id, task=manual_task), manual_task),
        ],
    )

    out = await get_autorun(
        pid=proj.id,
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert out.enabled is True
    assert out.max_fix_loops == 5
    assert out.auto_run_daily_cap == 10
    assert "backlog" in out.eligible_task_statuses
    assert "ready" in out.eligible_task_statuses
    assert "needs_fixes" in out.eligible_task_statuses
    # `_runs_today_count` and the recent listing both walk the same fake row
    # set, so we expect 1 auto + 2 recent.
    assert out.runs_today == 1
    assert len(out.recent_runs) == 2
    assert sum(1 for r in out.recent_runs if r.auto_triggered) == 1
    # daily_cap_remaining = max(0, 10 - 1) = 9
    assert out.daily_cap_remaining == 9


@pytest.mark.asyncio
async def test_get_autorun_returns_403_for_other_users_project() -> None:
    from daedalus.api.routes.autorun import get_autorun

    owner = _make_user()
    intruder = _make_user()
    proj = _make_project(owner_id=owner.id)
    db = _FakeSession(project=proj)

    with pytest.raises(HTTPException) as exc:
        await get_autorun(
            pid=proj.id,
            user=intruder,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_autorun_returns_404_when_project_missing() -> None:
    from daedalus.api.routes.autorun import get_autorun

    user = _make_user()
    proj = _make_project(owner_id=user.id)
    db = _FakeSession(project=proj)

    with pytest.raises(HTTPException) as exc:
        await get_autorun(
            pid=uuid.uuid4(),  # not the project's id
            user=user,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_autorun_persists_fields_and_audits() -> None:
    from daedalus.api.routes.autorun import patch_autorun
    from daedalus.api.schemas import AutoRunConfigPatch
    from daedalus.db.models import AuditEvent

    user = _make_user()
    proj = _make_project(owner_id=user.id)
    db = _FakeSession(project=proj)

    out = await patch_autorun(
        pid=proj.id,
        body=AutoRunConfigPatch(
            auto_run_fix=True,
            max_fix_loops=4,
            auto_run_quiet_hours_start=22,
            auto_run_quiet_hours_end=6,
            auto_run_daily_cap=5,
        ),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert proj.auto_run_fix is True
    assert proj.max_fix_loops == 4
    assert proj.auto_run_quiet_hours_start == 22
    assert proj.auto_run_quiet_hours_end == 6
    assert proj.auto_run_daily_cap == 5
    assert db.commit_count == 1
    audits = [o for o in db.added if isinstance(o, AuditEvent)]
    assert len(audits) == 1
    assert audits[0].action == "project.autorun.update"
    # PATCH response is the full status (re-derived) so the panel can
    # update in-place without a follow-up GET.
    assert out.enabled is True
    assert out.auto_run_daily_cap == 5


@pytest.mark.asyncio
async def test_patch_autorun_rejects_partial_quiet_hours() -> None:
    from daedalus.api.routes.autorun import patch_autorun
    from daedalus.api.schemas import AutoRunConfigPatch

    user = _make_user()
    proj = _make_project(owner_id=user.id)
    db = _FakeSession(project=proj)

    with pytest.raises(HTTPException) as exc:
        await patch_autorun(
            pid=proj.id,
            body=AutoRunConfigPatch(auto_run_quiet_hours_start=22),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400
    assert "quiet_hours_start" in exc.value.detail
    # Bail-out should not have committed anything.
    assert db.commit_count == 0
    assert proj.auto_run_quiet_hours_start is None


@pytest.mark.asyncio
async def test_patch_autorun_returns_403_for_other_users_project() -> None:
    from daedalus.api.routes.autorun import patch_autorun
    from daedalus.api.schemas import AutoRunConfigPatch

    owner = _make_user()
    intruder = _make_user()
    proj = _make_project(owner_id=owner.id)
    db = _FakeSession(project=proj)

    with pytest.raises(HTTPException) as exc:
        await patch_autorun(
            pid=proj.id,
            body=AutoRunConfigPatch(auto_run_fix=True),
            request=_fake_request(),
            user=intruder,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 403
    assert proj.auto_run_fix is False
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_patch_autorun_accepts_clearing_both_quiet_bounds() -> None:
    """Setting both ends to None must be allowed — that's how the panel
    turns the quiet-hours window off after it was previously enabled."""
    from daedalus.api.routes.autorun import patch_autorun
    from daedalus.api.schemas import AutoRunConfigPatch

    user = _make_user()
    proj = _make_project(
        owner_id=user.id,
        auto_run_quiet_hours_start=22,
        auto_run_quiet_hours_end=6,
    )
    db = _FakeSession(project=proj)

    await patch_autorun(
        pid=proj.id,
        body=AutoRunConfigPatch(
            auto_run_quiet_hours_start=None,
            auto_run_quiet_hours_end=None,
        ),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert proj.auto_run_quiet_hours_start is None
    assert proj.auto_run_quiet_hours_end is None
    assert db.commit_count == 1


# ---------- policy fields: concurrency / hourly / eligible / connectors ----


@pytest.mark.asyncio
async def test_get_autorun_returns_new_policy_fields_and_usage() -> None:
    """Status response surfaces concurrency / hourly caps + window utilization."""
    from daedalus.api.routes.autorun import get_autorun
    from daedalus.db.models import RunState

    user = _make_user()
    proj = _make_project(
        owner_id=user.id,
        auto_run_fix=True,
        auto_run_concurrency_cap=2,
        auto_run_hourly_cap=5,
        auto_run_allowed_connectors=["claude", "echo"],
        auto_run_eligible_statuses=["backlog", "needs_fixes"],
    )

    now = datetime.now(timezone.utc)
    # 1 active auto-run (running), 1 finished auto-run in the last hour, and
    # 1 non-auto manual run that must NOT count toward any cap.
    fix_task = _make_task(project_id=proj.id, tags=["fix-loop"], title="fix")
    manual_task = _make_task(project_id=proj.id, tags=[], title="manual")
    active_run = _make_run(project_id=proj.id, task=fix_task, created_at=now)
    active_run.state = RunState.running
    recent_run = _make_run(
        project_id=proj.id,
        task=fix_task,
        created_at=now - timedelta(minutes=15),
    )
    manual_run = _make_run(project_id=proj.id, task=manual_task, created_at=now)

    db = _FakeSession(
        project=proj,
        run_rows=[
            (active_run, fix_task),
            (recent_run, fix_task),
            (manual_run, manual_task),
        ],
    )

    out = await get_autorun(pid=proj.id, user=user, db=db)  # type: ignore[arg-type]

    assert out.auto_run_concurrency_cap == 2
    assert out.auto_run_hourly_cap == 5
    assert out.auto_run_allowed_connectors == ["claude", "echo"]
    assert sorted(out.auto_run_eligible_statuses) == ["backlog", "needs_fixes"]
    # `eligible_task_statuses` is the legacy alias — must mirror.
    assert sorted(out.eligible_task_statuses) == ["backlog", "needs_fixes"]
    # Two auto-triggered runs in the last hour, one of them still active.
    assert out.runs_last_hour == 2
    assert out.active_auto_runs == 1
    assert out.hourly_cap_remaining == 3
    assert out.concurrency_remaining == 1


@pytest.mark.asyncio
async def test_patch_autorun_persists_new_policy_fields() -> None:
    from daedalus.api.routes.autorun import patch_autorun
    from daedalus.api.schemas import AutoRunConfigPatch
    from daedalus.db.models import TaskStatus

    user = _make_user()
    proj = _make_project(owner_id=user.id)
    db = _FakeSession(project=proj)

    await patch_autorun(
        pid=proj.id,
        body=AutoRunConfigPatch(
            auto_run_concurrency_cap=4,
            auto_run_hourly_cap=20,
            auto_run_allowed_connectors=["claude"],
            auto_run_eligible_statuses=[TaskStatus.backlog, TaskStatus.ready],
        ),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert proj.auto_run_concurrency_cap == 4
    assert proj.auto_run_hourly_cap == 20
    assert proj.auto_run_allowed_connectors == ["claude"]
    # Enum list normalises to the raw string values for the ARRAY(Text)
    # column the scheduler reads.
    assert proj.auto_run_eligible_statuses == ["backlog", "ready"]


def test_autorun_config_patch_rejects_invalid_status() -> None:
    from daedalus.api.schemas import AutoRunConfigPatch

    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate(
            {"auto_run_eligible_statuses": ["not-a-real-status"]}
        )


def test_autorun_config_patch_rejects_oversized_caps() -> None:
    from daedalus.api.schemas import AutoRunConfigPatch

    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"auto_run_concurrency_cap": 999})
    with pytest.raises(ValueError):
        AutoRunConfigPatch.model_validate({"auto_run_hourly_cap": -1})


# ---------- global defaults singleton ---------------------------------------


class _FakeDefaultsSession(_FakeSession):
    """Adds a singleton-row fake for the AutoRunDefaults table."""

    def __init__(self, *, defaults_row=None, **kw):
        super().__init__(project=None, **kw)
        self._defaults = defaults_row

    async def get(self, model, pid):
        if getattr(model, "__name__", "") == "AutoRunDefaults":
            return self._defaults
        return await super().get(model, pid)


def _make_defaults_row(**overrides):
    from daedalus.db.models import AutoRunDefaults

    row = AutoRunDefaults(id=1)
    row.enabled = overrides.get("enabled", False)
    row.max_fix_loops = overrides.get("max_fix_loops", 3)
    row.daily_cap = overrides.get("daily_cap", 0)
    row.hourly_cap = overrides.get("hourly_cap", 0)
    row.concurrency_cap = overrides.get("concurrency_cap", 1)
    row.quiet_hours_start = overrides.get("quiet_hours_start")
    row.quiet_hours_end = overrides.get("quiet_hours_end")
    row.eligible_statuses = overrides.get(
        "eligible_statuses", ["backlog", "ready", "needs_fixes"]
    )
    row.allowed_connectors = overrides.get("allowed_connectors", [])
    row.created_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    return row


@pytest.mark.asyncio
async def test_get_autorun_defaults_returns_singleton() -> None:
    from daedalus.api.routes.autorun import get_autorun_defaults

    user = _make_user()
    defaults = _make_defaults_row(daily_cap=12, hourly_cap=3)
    db = _FakeDefaultsSession(defaults_row=defaults)

    out = await get_autorun_defaults(user=user, db=db)  # type: ignore[arg-type]
    assert out.daily_cap == 12
    assert out.hourly_cap == 3


@pytest.mark.asyncio
async def test_patch_autorun_defaults_owner_only() -> None:
    from daedalus.api.routes.autorun import patch_autorun_defaults
    from daedalus.api.schemas import AutoRunDefaultsPatch

    member = _make_user("member")
    defaults = _make_defaults_row()
    db = _FakeDefaultsSession(defaults_row=defaults)

    with pytest.raises(HTTPException) as exc:
        await patch_autorun_defaults(
            body=AutoRunDefaultsPatch(enabled=True),
            request=_fake_request(),
            user=member,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 403
    assert defaults.enabled is False


@pytest.mark.asyncio
async def test_patch_autorun_defaults_persists_and_audits() -> None:
    from daedalus.api.routes.autorun import patch_autorun_defaults
    from daedalus.api.schemas import AutoRunDefaultsPatch
    from daedalus.db.models import AuditEvent, TaskStatus

    owner = _make_user("owner")
    defaults = _make_defaults_row()
    db = _FakeDefaultsSession(defaults_row=defaults)

    out = await patch_autorun_defaults(
        body=AutoRunDefaultsPatch(
            enabled=True,
            daily_cap=50,
            hourly_cap=10,
            concurrency_cap=2,
            allowed_connectors=["claude"],
            eligible_statuses=[TaskStatus.backlog, TaskStatus.needs_fixes],
        ),
        request=_fake_request(),
        user=owner,
        db=db,  # type: ignore[arg-type]
    )

    assert defaults.enabled is True
    assert defaults.daily_cap == 50
    assert defaults.allowed_connectors == ["claude"]
    assert defaults.eligible_statuses == ["backlog", "needs_fixes"]
    audits = [o for o in db.added if isinstance(o, AuditEvent)]
    assert len(audits) == 1
    assert audits[0].action == "autorun.defaults.update"
    assert out.enabled is True


@pytest.mark.asyncio
async def test_patch_autorun_defaults_rejects_partial_quiet_hours() -> None:
    from daedalus.api.routes.autorun import patch_autorun_defaults
    from daedalus.api.schemas import AutoRunDefaultsPatch

    owner = _make_user("owner")
    defaults = _make_defaults_row()
    db = _FakeDefaultsSession(defaults_row=defaults)

    with pytest.raises(HTTPException) as exc:
        await patch_autorun_defaults(
            body=AutoRunDefaultsPatch(quiet_hours_start=22),
            request=_fake_request(),
            user=owner,
            db=db,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400
    assert defaults.quiet_hours_start is None
