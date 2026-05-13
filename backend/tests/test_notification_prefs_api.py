"""Coverage for /account/notification-prefs route handlers."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


# ── fakes ────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """AsyncSession stand-in for the prefs route.

    Routes the single SELECT statement (`UserNotificationPref` by user_id)
    against an in-memory dict and records mutations so tests can assert
    on them.
    """

    def __init__(self, *, prefs=None):
        self._prefs = {p.user_id: p for p in (prefs or [])}
        self.added: list = []
        self.commit_count = 0
        self.flush_count = 0
        self.refresh_count = 0

    async def execute(self, stmt):
        # Pull the bound parameter — every query in this route filters on
        # `user_id`, so the first bind value is the lookup key.
        compiled = stmt.compile()
        params = list(compiled.params.values())
        target_id = params[0] if params else None
        return _FakeResult(self._prefs.get(target_id))

    def add(self, obj) -> None:
        self.added.append(obj)
        if hasattr(obj, "user_id"):
            self._prefs[obj.user_id] = obj

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1

    async def refresh(self, _obj) -> None:
        self.refresh_count += 1

    async def delete(self, obj) -> None:
        self._prefs.pop(getattr(obj, "user_id", None), None)


def _make_user():
    from daedalus.db.models import Role

    return SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        display_name="User",
        role=Role("member"),
    )


def _fake_request():
    return SimpleNamespace(state=SimpleNamespace(cert_fp="fp:test"))


# ── schema tests ─────────────────────────────────────────────────────────


def test_patch_schema_excludes_unset_fields() -> None:
    """Only fields the client actually sent are preserved through model_dump."""
    from daedalus.api.schemas import NotificationPrefsPatch

    patch = NotificationPrefsPatch.model_validate({"email_task_completed": False})
    assert patch.model_dump(exclude_unset=True) == {"email_task_completed": False}


def test_patch_schema_preserves_explicit_null_threshold() -> None:
    """`null` must round-trip so users can clear the usage gate."""
    from daedalus.api.schemas import NotificationPrefsPatch

    patch = NotificationPrefsPatch.model_validate({"usage_threshold_micros": None})
    dump = patch.model_dump(exclude_unset=True)
    assert "usage_threshold_micros" in dump
    assert dump["usage_threshold_micros"] is None


def test_patch_schema_rejects_unknown_keys() -> None:
    from daedalus.api.schemas import NotificationPrefsPatch

    with pytest.raises(ValueError):
        NotificationPrefsPatch.model_validate({"not_a_field": True})


def test_patch_schema_rejects_negative_threshold() -> None:
    from daedalus.api.schemas import NotificationPrefsPatch

    with pytest.raises(ValueError):
        NotificationPrefsPatch.model_validate({"usage_threshold_micros": -1})


# ── GET /notification-prefs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_defaults_when_no_row() -> None:
    from daedalus.api.routes.notification_prefs import get_notification_prefs

    user = _make_user()
    db = _FakeSession()

    result = await get_notification_prefs(user=user, db=db)  # type: ignore[arg-type]

    assert result["email_task_completed"] is True
    assert result["in_app_usage_threshold"] is True
    assert result["usage_threshold_micros"] is None


@pytest.mark.asyncio
async def test_get_returns_existing_row() -> None:
    from daedalus.api.routes.notification_prefs import get_notification_prefs
    from daedalus.db.models import UserNotificationPref

    user = _make_user()
    pref = UserNotificationPref(
        id=uuid.uuid4(),
        user_id=user.id,
        email_task_completed=False,
        email_task_failed=True,
        email_task_needs_fixes=True,
        email_usage_threshold=False,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=5_000_000,
    )
    db = _FakeSession(prefs=[pref])

    result = await get_notification_prefs(user=user, db=db)  # type: ignore[arg-type]

    assert result["email_task_completed"] is False
    assert result["email_usage_threshold"] is False
    assert result["usage_threshold_micros"] == 5_000_000


# ── PATCH /notification-prefs ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_lazy_inserts_when_no_row() -> None:
    """First write for a user must insert a row seeded from defaults."""
    from daedalus.api.routes.notification_prefs import patch_notification_prefs
    from daedalus.api.schemas import NotificationPrefsPatch
    from daedalus.db.models import AuditEvent, UserNotificationPref

    user = _make_user()
    db = _FakeSession()

    result = await patch_notification_prefs(
        body=NotificationPrefsPatch.model_validate({"email_task_failed": False}),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    inserted = [obj for obj in db.added if isinstance(obj, UserNotificationPref)]
    assert len(inserted) == 1
    assert inserted[0].user_id == user.id
    assert inserted[0].email_task_failed is False
    assert inserted[0].email_task_completed is True  # default preserved
    assert result["email_task_failed"] is False
    assert db.commit_count == 1
    audits = [obj for obj in db.added if isinstance(obj, AuditEvent)]
    assert len(audits) == 1
    assert audits[0].action == "notification_prefs.update"


@pytest.mark.asyncio
async def test_patch_updates_existing_row_partially() -> None:
    from daedalus.api.routes.notification_prefs import patch_notification_prefs
    from daedalus.api.schemas import NotificationPrefsPatch
    from daedalus.db.models import UserNotificationPref

    user = _make_user()
    pref = UserNotificationPref(
        id=uuid.uuid4(),
        user_id=user.id,
        email_task_completed=True,
        email_task_failed=True,
        email_task_needs_fixes=True,
        email_usage_threshold=True,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=None,
    )
    db = _FakeSession(prefs=[pref])

    await patch_notification_prefs(
        body=NotificationPrefsPatch.model_validate(
            {"email_task_completed": False, "usage_threshold_micros": 1_000_000}
        ),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert pref.email_task_completed is False
    assert pref.email_task_failed is True  # untouched
    assert pref.usage_threshold_micros == 1_000_000


@pytest.mark.asyncio
async def test_patch_clears_threshold_when_null() -> None:
    from daedalus.api.routes.notification_prefs import patch_notification_prefs
    from daedalus.api.schemas import NotificationPrefsPatch
    from daedalus.db.models import UserNotificationPref

    user = _make_user()
    pref = UserNotificationPref(
        id=uuid.uuid4(),
        user_id=user.id,
        email_task_completed=True,
        email_task_failed=True,
        email_task_needs_fixes=True,
        email_usage_threshold=True,
        in_app_task_completed=True,
        in_app_task_failed=True,
        in_app_task_needs_fixes=True,
        in_app_usage_threshold=True,
        usage_threshold_micros=10_000_000,
    )
    db = _FakeSession(prefs=[pref])

    await patch_notification_prefs(
        body=NotificationPrefsPatch.model_validate({"usage_threshold_micros": None}),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert pref.usage_threshold_micros is None


@pytest.mark.asyncio
async def test_patch_no_op_when_body_empty_does_not_insert() -> None:
    """Empty patch must be a graceful no-op — no row inserted, no commit."""
    from daedalus.api.routes.notification_prefs import patch_notification_prefs
    from daedalus.api.schemas import NotificationPrefsPatch
    from daedalus.db.models import AuditEvent, UserNotificationPref

    user = _make_user()
    db = _FakeSession()

    result = await patch_notification_prefs(
        body=NotificationPrefsPatch.model_validate({}),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert not any(isinstance(o, UserNotificationPref) for o in db.added)
    assert not any(isinstance(o, AuditEvent) for o in db.added)
    assert db.commit_count == 0
    # And it must still echo the defaults so the SPA can hydrate.
    assert result["email_task_completed"] is True


# ── POST /notification-prefs/test-email ──────────────────────────────────


@pytest.mark.asyncio
async def test_test_email_invokes_channel_and_audits(monkeypatch) -> None:
    from daedalus.api.routes import notification_prefs as route_mod
    from daedalus.db.models import AuditEvent

    user = _make_user()
    db = _FakeSession()

    sent: list[tuple] = []

    class _OkChannel:
        name = "email"

        async def send(self, event, user) -> None:  # noqa: ANN001
            sent.append((event, user))

    monkeypatch.setattr(route_mod, "EmailChannel", lambda: _OkChannel())

    payload = await route_mod.send_test_email(
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert payload == {"status": "sent", "to": user.email}
    assert len(sent) == 1
    audits = [obj for obj in db.added if isinstance(obj, AuditEvent)]
    assert len(audits) == 1
    assert audits[0].action == "notification_prefs.test_email"
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_test_email_surfaces_smtp_failure_as_502(monkeypatch) -> None:
    from daedalus.api.routes import notification_prefs as route_mod

    user = _make_user()
    db = _FakeSession()

    class _BadChannel:
        name = "email"

        async def send(self, event, user) -> None:  # noqa: ANN001
            raise RuntimeError("smtp down")

    monkeypatch.setattr(route_mod, "EmailChannel", lambda: _BadChannel())

    with pytest.raises(HTTPException) as excinfo:
        await route_mod.send_test_email(
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 502
    assert "smtp down" in excinfo.value.detail
