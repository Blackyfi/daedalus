"""Integration tests for the notification fan-out + usage threshold gate.

The CI environment has no async DB driver, so we substitute a hand-rolled
AsyncSession that backs the few `select(...)` queries the dispatcher and
the usage monitor actually issue. The recipient lookup, pref lookup, and
project cost aggregation are all driven by tiny in-memory tables. What
we verify is *which* channels were called for *which* user given a set
of preferences — the dispatcher's pref→channel evaluation logic — not
the bytes those channels would have emitted.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from daedalus.db.models import (
    Project,
    Run,
    RunKind,
    RunState,
    Task,
    TaskStatus,
    User,
    UserNotificationPref,
)
from daedalus.notifications.dispatcher import notify
from daedalus.notifications.events import NotificationEvent, NotificationKind
from daedalus.notifications.usage_monitor import maybe_notify_usage_threshold


# ── recording channel ────────────────────────────────────────────────────


class _RecordingChannel:
    """Test channel: records every (event, user) it receives.

    Used in place of EmailChannel/InAppChannel so the dispatcher's
    pref-evaluation logic can be inspected without touching SMTP/Redis.
    """

    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[NotificationEvent, str]] = []

    async def send(self, event: NotificationEvent, user: User) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} simulated failure")
        self.calls.append((event, user.email))


# ── in-memory fake AsyncSession ──────────────────────────────────────────


@dataclass
class _Store:
    users: dict[uuid.UUID, User] = field(default_factory=dict)
    projects: dict[uuid.UUID, Project] = field(default_factory=dict)
    tasks: dict[uuid.UUID, Task] = field(default_factory=dict)
    runs: dict[uuid.UUID, Run] = field(default_factory=dict)
    prefs: dict[uuid.UUID, UserNotificationPref] = field(default_factory=dict)


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def all(self) -> list[Any]:
        return list(self._items)

    def unique(self) -> "_ScalarResult":
        return self

    def scalar_one_or_none(self) -> Any:
        if not self._items:
            return None
        return self._items[0]


class _ScalarsAccessor:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)

    def scalar_one(self) -> Any:
        return self._items[0] if self._items else 0

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class _FakeAsyncSession:
    """A tiny stand-in implementing only the surface the notifications
    module touches: `execute(select(...))`, `get(Model, id)`."""

    def __init__(self, store: _Store) -> None:
        self.store = store

    async def execute(self, stmt) -> _ScalarsAccessor:
        return _execute(self.store, stmt)

    async def get(self, model, ident):
        if model is Project:
            return self.store.projects.get(ident)
        if model is User:
            return self.store.users.get(ident)
        if model is Task:
            return self.store.tasks.get(ident)
        if model is Run:
            return self.store.runs.get(ident)
        return None


def _execute(store: _Store, stmt) -> _ScalarsAccessor:
    """Pattern-match the few queries the notifications code emits.

    We render the compiled statement to a string and key off the FROM
    clause + columns. This is brittle by design — the alternative is a
    full sqlalchemy core re-implementation, which is far worse.
    """
    text = str(stmt).lower()

    # `_resolve_project_users`: SELECT users JOIN projects ON ... WHERE projects.id = :id
    if "from users" in text and "join projects" in text:
        target_id = _first_bind_uuid(stmt)
        out = [
            store.users[p.owner_id]
            for p in store.projects.values()
            if p.id == target_id and p.owner_id in store.users
        ]
        return _ScalarsAccessor(out)

    # `_load_prefs`: SELECT user_notification_prefs WHERE user_id IN (...)
    if "from user_notification_prefs" in text:
        ids = _bind_uuid_list(stmt)
        out = [p for p in store.prefs.values() if p.user_id in ids]
        return _ScalarsAccessor(out)

    # `_owner_pref`: SELECT user_notification_prefs WHERE user_id = :id
    # (handled by the FROM check above — same predicate kind)

    # `_project_cost_micros`: SELECT coalesce(sum(runs.cost_usd_micros), 0) WHERE project_id = :id
    if "sum" in text and "cost_usd_micros" in text:
        pid = _first_bind_uuid(stmt)
        total = sum(
            (r.cost_usd_micros or 0)
            for r in store.runs.values()
            if r.project_id == pid
        )
        return _ScalarsAccessor([total])

    return _ScalarsAccessor([])


def _bind_uuid_list(stmt) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    try:
        compiled = stmt.compile()
        for value in compiled.params.values():
            if isinstance(value, uuid.UUID):
                out.add(value)
            elif isinstance(value, (list, tuple, set)):
                for v in value:
                    if isinstance(v, uuid.UUID):
                        out.add(v)
            elif isinstance(value, str):
                try:
                    out.add(uuid.UUID(value))
                except ValueError:
                    pass
    except Exception:
        pass
    return out


def _first_bind_uuid(stmt) -> uuid.UUID | None:
    ids = _bind_uuid_list(stmt)
    return next(iter(ids), None)


# ── fixtures / builders ──────────────────────────────────────────────────


def _make_user(store: _Store, email: str = "owner@example.com") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name="Owner",
        password_hash="",
        recovery_codes_hash=[],
    )
    store.users[user.id] = user
    return user


def _make_project(store: _Store, owner: User, name: str = "Demo") -> Project:
    p = Project(
        id=uuid.uuid4(),
        owner_id=owner.id,
        name=name,
        workspace_path="/tmp/demo",
    )
    store.projects[p.id] = p
    return p


def _make_task(store: _Store, project: Project, title: str = "Build it") -> Task:
    t = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        title=title,
        depends_on=[],
        tags=[],
    )
    store.tasks[t.id] = t
    return t


def _make_run(
    store: _Store,
    project: Project,
    task: Task | None = None,
    *,
    cost_usd_micros: int | None = None,
    state: RunState = RunState.completed,
) -> Run:
    r = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        task_id=task.id if task else None,
        kind=RunKind.task,
        state=state,
        connector_snapshot={},
        cost_usd_micros=cost_usd_micros,
    )
    store.runs[r.id] = r
    return r


# ── dispatcher tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_pref_emits_on_all_channels() -> None:
    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    session = _FakeAsyncSession(store)
    email_chan = _RecordingChannel("email")
    in_app_chan = _RecordingChannel("in_app")

    event = NotificationEvent(
        kind=NotificationKind.task_completed,
        title="Done",
        body="Hi",
        project_id=project.id,
    )
    delivered = await notify(event, session, channels=[email_chan, in_app_chan])

    assert delivered == 2
    assert [c[1] for c in email_chan.calls] == ["owner@example.com"]
    assert [c[1] for c in in_app_chan.calls] == ["owner@example.com"]


@pytest.mark.asyncio
async def test_pref_disables_specific_channel_for_kind() -> None:
    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    pref = UserNotificationPref(
        id=uuid.uuid4(),
        user_id=owner.id,
        email_task_completed=True,
        email_task_failed=False,  # muted
        email_task_needs_fixes=True,
        email_usage_threshold=True,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=None,
    )
    store.prefs[owner.id] = pref

    session = _FakeAsyncSession(store)
    email_chan = _RecordingChannel("email")
    in_app_chan = _RecordingChannel("in_app")

    event = NotificationEvent(
        kind=NotificationKind.task_failed,
        title="Run blew up",
        body="bad",
        project_id=project.id,
    )
    delivered = await notify(event, session, channels=[email_chan, in_app_chan])

    assert delivered == 1
    assert email_chan.calls == []
    assert len(in_app_chan.calls) == 1


@pytest.mark.asyncio
async def test_failing_channel_does_not_block_other_channels() -> None:
    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    session = _FakeAsyncSession(store)
    bad = _RecordingChannel("email", fail=True)
    good = _RecordingChannel("in_app")

    event = NotificationEvent(
        kind=NotificationKind.task_completed,
        title="ok",
        body="ok",
        project_id=project.id,
    )
    delivered = await notify(event, session, channels=[bad, good])

    assert delivered == 1
    assert good.calls and good.calls[0][1] == "owner@example.com"


@pytest.mark.asyncio
async def test_unknown_project_emits_nothing() -> None:
    store = _Store()
    session = _FakeAsyncSession(store)
    chan = _RecordingChannel("email")

    event = NotificationEvent(
        kind=NotificationKind.task_completed,
        title="x",
        body="x",
        project_id=uuid.uuid4(),
    )
    delivered = await notify(event, session, channels=[chan])

    assert delivered == 0
    assert chan.calls == []


# ── usage-threshold tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_threshold_fires_only_once_per_crossing(monkeypatch) -> None:
    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    store.prefs[owner.id] = UserNotificationPref(
        id=uuid.uuid4(),
        user_id=owner.id,
        email_task_completed=True,
        email_task_failed=True,
        email_task_needs_fixes=True,
        email_usage_threshold=True,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=5_000_000,  # $5
    )
    session = _FakeAsyncSession(store)

    chan = _RecordingChannel("email")
    in_app = _RecordingChannel("in_app")

    from daedalus.notifications import dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_CHANNELS", (chan, in_app))

    # First run: $3 — below threshold, no notification.
    run1 = _make_run(store, project, cost_usd_micros=3_000_000)
    crossings = await maybe_notify_usage_threshold(
        session,
        project_id=project.id,
        run_id=run1.id,
        delta_cost_micros=3_000_000,
    )
    assert crossings == 0
    assert chan.calls == []

    # Second run: +$3 → total $6, crosses $5. Fires once.
    run2 = _make_run(store, project, cost_usd_micros=3_000_000)
    crossings = await maybe_notify_usage_threshold(
        session,
        project_id=project.id,
        run_id=run2.id,
        delta_cost_micros=3_000_000,
    )
    assert crossings == 1
    assert len(chan.calls) == 1
    assert len(in_app.calls) == 1

    # Third run: still over the threshold but the previous total was
    # already above it — must not re-fire.
    run3 = _make_run(store, project, cost_usd_micros=1_000_000)
    crossings = await maybe_notify_usage_threshold(
        session,
        project_id=project.id,
        run_id=run3.id,
        delta_cost_micros=1_000_000,
    )
    assert crossings == 0
    assert len(chan.calls) == 1  # unchanged


@pytest.mark.asyncio
async def test_usage_threshold_skipped_when_pref_disabled(monkeypatch) -> None:
    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    # No UserNotificationPref row → no threshold configured.
    session = _FakeAsyncSession(store)

    chan = _RecordingChannel("email")
    from daedalus.notifications import dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_CHANNELS", (chan,))

    run = _make_run(store, project, cost_usd_micros=10_000_000)
    crossings = await maybe_notify_usage_threshold(
        session,
        project_id=project.id,
        run_id=run.id,
        delta_cost_micros=10_000_000,
    )
    assert crossings == 0
    assert chan.calls == []


# ── task-lifecycle integration via scheduler helper ──────────────────────


@pytest.mark.asyncio
async def test_scheduler_lifecycle_emits_task_completed(monkeypatch) -> None:
    """The scheduler hook delegates to notify() with the right kind/IDs."""
    from daedalus.hermes import scheduler as scheduler_mod
    from daedalus.notifications import dispatcher as dispatcher_mod

    chan = _RecordingChannel("email")
    in_app = _RecordingChannel("in_app")
    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_CHANNELS", (chan, in_app))

    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    task = _make_task(store, project)
    run = _make_run(store, project, task, state=RunState.completed)
    session = _FakeAsyncSession(store)

    sched = scheduler_mod.HermesScheduler.__new__(scheduler_mod.HermesScheduler)
    await sched._dispatch_lifecycle_notifications(session, run)

    assert len(chan.calls) == 1
    event, _email = chan.calls[0]
    assert event.kind == NotificationKind.task_completed
    assert event.task_id == task.id
    assert event.run_id == run.id
    assert event.project_id == project.id


@pytest.mark.asyncio
async def test_scheduler_lifecycle_emits_needs_fixes_when_task_marked(monkeypatch) -> None:
    from daedalus.hermes import scheduler as scheduler_mod
    from daedalus.notifications import dispatcher as dispatcher_mod

    chan = _RecordingChannel("email")
    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_CHANNELS", (chan,))

    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    task = _make_task(store, project)
    task.status = TaskStatus.needs_fixes
    run = _make_run(store, project, task, state=RunState.failed)
    session = _FakeAsyncSession(store)

    sched = scheduler_mod.HermesScheduler.__new__(scheduler_mod.HermesScheduler)
    await sched._dispatch_lifecycle_notifications(session, run)

    assert len(chan.calls) == 1
    assert chan.calls[0][0].kind == NotificationKind.task_needs_fixes


@pytest.mark.asyncio
async def test_scheduler_lifecycle_skips_non_task_runs(monkeypatch) -> None:
    from daedalus.hermes import scheduler as scheduler_mod
    from daedalus.notifications import dispatcher as dispatcher_mod

    chan = _RecordingChannel("email")
    monkeypatch.setattr(dispatcher_mod, "_DEFAULT_CHANNELS", (chan,))

    store = _Store()
    owner = _make_user(store)
    project = _make_project(store, owner)
    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        kind=RunKind.argus,
        state=RunState.completed,
        connector_snapshot={},
    )
    store.runs[run.id] = run
    session = _FakeAsyncSession(store)

    sched = scheduler_mod.HermesScheduler.__new__(scheduler_mod.HermesScheduler)
    await sched._dispatch_lifecycle_notifications(session, run)

    assert chan.calls == []
