"""Coverage for `PATCH /ideas/{iid}` — schema aliasing, auth, and the
post-promotion editability rule."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import Select


# Tests for the IdeaPatch schema --------------------------------------------------

def test_idea_patch_accepts_text_field() -> None:
    from daedalus.api.schemas import IdeaPatch

    patch = IdeaPatch.model_validate({"text": "rewritten idea"})
    assert patch.text == "rewritten idea"
    assert patch.tags is None
    assert patch.sort_index is None


def test_idea_patch_accepts_body_alias() -> None:
    from daedalus.api.schemas import IdeaPatch

    patch = IdeaPatch.model_validate({"body": "rewritten via body alias"})
    assert patch.text == "rewritten via body alias"


def test_idea_patch_rejects_empty_text() -> None:
    from daedalus.api.schemas import IdeaPatch

    with pytest.raises(ValueError):
        IdeaPatch.model_validate({"text": ""})


def test_idea_patch_does_not_expose_archived_toggle() -> None:
    """The promote-to-task flow owns `archived`; the PATCH surface should not."""
    from daedalus.api.schemas import IdeaPatch

    patch = IdeaPatch.model_validate({"text": "x", "archived": True})
    # Unknown extras are silently dropped by Pydantic; the important thing is
    # that the model exposes no `archived` attribute that would later be
    # `setattr`'d onto the ORM row.
    assert not hasattr(patch, "archived")


# Fakes that pretend to be the bits of SQLAlchemy / FastAPI the route touches.

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Minimal AsyncSession stand-in: dispatches `execute(select(X))` based on
    the entity in `column_descriptions`, records `add()` calls, and tracks
    whether `commit()` was reached."""

    def __init__(self, *, ideas=None, projects=None):
        self._ideas = {i.id: i for i in (ideas or [])}
        self._projects = {p.id: p for p in (projects or [])}
        self.added: list = []
        self.commit_count = 0
        self.flush_count = 0

    async def execute(self, stmt: Select):
        entity = stmt.column_descriptions[0]["entity"]
        # Pick whichever id literal the WHERE clause was built with.
        target_id = stmt.whereclause.right.value  # type: ignore[union-attr]
        if entity.__name__ == "Idea":
            return _FakeResult(self._ideas.get(target_id))
        if entity.__name__ == "Project":
            return _FakeResult(self._projects.get(target_id))
        raise AssertionError(f"unexpected entity in fake session: {entity!r}")

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1


def _make_user(role_value: str = "member"):
    from daedalus.db.models import Role

    return SimpleNamespace(id=uuid.uuid4(), role=Role(role_value))


def _make_idea(*, project_id: uuid.UUID, archived: bool = False):
    from daedalus.db.models import Idea

    return Idea(
        id=uuid.uuid4(),
        project_id=project_id,
        text="original text",
        tags=["a"],
        archived=archived,
        sort_index=0,
    )


def _make_project(*, owner_id: uuid.UUID):
    from daedalus.db.models import Project

    return Project(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="proj",
        workspace_path="/tmp/proj",
    )


def _fake_request():
    return SimpleNamespace(state=SimpleNamespace(cert_fp="fp:test"))


# Route handler tests -----------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_idea_happy_path_updates_text_and_audits() -> None:
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch
    from daedalus.db.models import AuditEvent

    user = _make_user()
    project = _make_project(owner_id=user.id)
    idea = _make_idea(project_id=project.id)
    db = _FakeSession(ideas=[idea], projects=[project])
    original_updated_at = idea.updated_at  # may be None pre-flush

    result = await patch_idea(
        iid=idea.id,
        body=IdeaPatch(text="updated body"),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert result is idea
    assert idea.text == "updated body"
    assert idea.updated_at is not None and idea.updated_at != original_updated_at
    assert db.commit_count == 1

    audits = [obj for obj in db.added if isinstance(obj, AuditEvent)]
    assert len(audits) == 1
    assert audits[0].action == "idea.update"
    assert audits[0].target_kind == "idea"
    assert audits[0].target_id == str(idea.id)
    assert audits[0].payload == {"fields": ["text"]}
    assert audits[0].actor_user_id == user.id
    assert audits[0].actor_cert_fp == "fp:test"


@pytest.mark.asyncio
async def test_patch_idea_accepts_body_alias_in_request() -> None:
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch

    user = _make_user()
    project = _make_project(owner_id=user.id)
    idea = _make_idea(project_id=project.id)
    db = _FakeSession(ideas=[idea], projects=[project])

    body = IdeaPatch.model_validate({"body": "via alias"})
    await patch_idea(
        iid=idea.id,
        body=body,
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert idea.text == "via alias"


@pytest.mark.asyncio
async def test_patch_idea_returns_403_when_user_is_not_project_owner() -> None:
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch
    from daedalus.db.models import AuditEvent

    owner = _make_user()
    intruder = _make_user()  # role=member, different id
    project = _make_project(owner_id=owner.id)
    idea = _make_idea(project_id=project.id)
    db = _FakeSession(ideas=[idea], projects=[project])

    with pytest.raises(HTTPException) as excinfo:
        await patch_idea(
            iid=idea.id,
            body=IdeaPatch(text="hostile edit"),
            request=_fake_request(),
            user=intruder,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 403
    # The text must not have been mutated and no audit row should exist.
    assert idea.text == "original text"
    assert db.commit_count == 0
    assert not any(isinstance(o, AuditEvent) for o in db.added)


@pytest.mark.asyncio
async def test_patch_idea_returns_409_after_promotion() -> None:
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch
    from daedalus.db.models import AuditEvent

    user = _make_user()
    project = _make_project(owner_id=user.id)
    # Plan-confirm flips `archived` on every promoted idea — that's the
    # "non-editable" signal the route must honour.
    idea = _make_idea(project_id=project.id, archived=True)
    db = _FakeSession(ideas=[idea], projects=[project])

    with pytest.raises(HTTPException) as excinfo:
        await patch_idea(
            iid=idea.id,
            body=IdeaPatch(text="too late"),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 409
    assert "promoted" in excinfo.value.detail.lower()
    assert idea.text == "original text"
    assert db.commit_count == 0
    assert not any(isinstance(o, AuditEvent) for o in db.added)


@pytest.mark.asyncio
async def test_patch_idea_returns_404_when_missing() -> None:
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch

    user = _make_user()
    db = _FakeSession()

    with pytest.raises(HTTPException) as excinfo:
        await patch_idea(
            iid=uuid.uuid4(),
            body=IdeaPatch(text="anything"),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_idea_owner_role_can_edit_other_peoples_projects() -> None:
    """`Role.owner` is the platform-admin role and bypasses the per-project
    ownership check (matches the existing `_project` helper)."""
    from daedalus.api.routes.ideas import patch_idea
    from daedalus.api.schemas import IdeaPatch

    project_owner = _make_user()
    admin = _make_user(role_value="owner")
    project = _make_project(owner_id=project_owner.id)
    idea = _make_idea(project_id=project.id)
    db = _FakeSession(ideas=[idea], projects=[project])

    await patch_idea(
        iid=idea.id,
        body=IdeaPatch(text="admin edit"),
        request=_fake_request(),
        user=admin,
        db=db,  # type: ignore[arg-type]
    )

    assert idea.text == "admin edit"
    assert db.commit_count == 1
