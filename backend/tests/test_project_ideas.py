"""Coverage for the project-idea (Projects-page) CRUD + promote endpoints.

Mirrors the style of `tests/test_ideas_patch.py` — a fake `AsyncSession`
that resolves `select(Entity)` based on the entity in column_descriptions,
so we can exercise the route handlers in isolation without spinning up
Postgres.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch as mock_patch

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import Select


# ---- schema tests -----------------------------------------------------------


def test_project_idea_in_requires_text() -> None:
    from daedalus.api.schemas import ProjectIdeaIn

    with pytest.raises(ValueError):
        ProjectIdeaIn.model_validate({"text": ""})


def test_project_idea_patch_accepts_body_alias() -> None:
    from daedalus.api.schemas import ProjectIdeaPatch

    patch = ProjectIdeaPatch.model_validate({"body": "rename via alias"})
    assert patch.text == "rename via alias"


def test_project_idea_patch_accepts_status_archive() -> None:
    from daedalus.api.schemas import ProjectIdeaPatch
    from daedalus.db.models import ProjectIdeaStatus

    patch = ProjectIdeaPatch.model_validate({"status": "archived"})
    assert patch.status == ProjectIdeaStatus.archived


def test_promote_payload_defaults_init_git_false() -> None:
    from daedalus.api.schemas import ProjectIdeaPromote

    payload = ProjectIdeaPromote.model_validate(
        {"name": "demo", "workspace_path": "/workspaces/demo"}
    )
    assert payload.init_git is False
    assert payload.git_default_branch == "main"


# ---- shared fakes -----------------------------------------------------------


class _FakeResult:
    def __init__(self, value, multi: bool = False) -> None:
        self._value = value
        self._multi = multi

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._multi else []


class _FakeSession:
    def __init__(self, *, project_ideas=None, projects=None) -> None:
        self._ideas = {i.id: i for i in (project_ideas or [])}
        self._projects = {p.id: p for p in (projects or [])}
        self.added: list = []
        self.deleted: list = []
        self.commit_count = 0
        self.flush_count = 0

    async def execute(self, stmt: Select):
        entity = stmt.column_descriptions[0]["entity"]
        # `whereclause` is None for the bare list query.
        if stmt.whereclause is None:
            if entity.__name__ == "ProjectIdea":
                return _FakeResult(list(self._ideas.values()), multi=True)
            raise AssertionError(f"unexpected list query for {entity!r}")
        target_id = stmt.whereclause.right.value  # type: ignore[union-attr]
        if entity.__name__ == "ProjectIdea":
            return _FakeResult(self._ideas.get(target_id))
        raise AssertionError(f"unexpected entity in fake session: {entity!r}")

    def add(self, obj) -> None:
        # Assign an id if the ORM hasn't already; mimics the post-flush state.
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1
        # Mimic Postgres assigning ids on flush.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


def _make_user(role_value: str = "member"):
    from daedalus.db.models import Role

    return SimpleNamespace(id=uuid.uuid4(), role=Role(role_value))


def _make_project_idea(*, owner_id: uuid.UUID, status_value: str = "new"):
    from daedalus.db.models import ProjectIdea, ProjectIdeaStatus

    return ProjectIdea(
        id=uuid.uuid4(),
        owner_id=owner_id,
        text="hack on a markdown indexer cli",
        tags=["cli"],
        status=ProjectIdeaStatus(status_value),
        sort_index=0,
    )


def _fake_request():
    return SimpleNamespace(state=SimpleNamespace(cert_fp="fp:test"))


# ---- route handler tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_idea_persists_and_audits() -> None:
    from daedalus.api.routes.project_ideas import create_project_idea
    from daedalus.api.schemas import ProjectIdeaIn
    from daedalus.db.models import AuditEvent, ProjectIdea, ProjectIdeaStatus

    user = _make_user()
    db = _FakeSession()

    result = await create_project_idea(
        body=ProjectIdeaIn(text="build a tiny static site generator", tags=["go"]),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert isinstance(result, ProjectIdea)
    assert result.owner_id == user.id
    assert result.text == "build a tiny static site generator"
    # The status column has a server-side default; with no Postgres in the
    # loop the in-memory ORM attribute stays None until the row is reloaded.
    # The next two assertions cover what the route *did* (no early
    # archive/promotion shenanigans).
    assert result.status in (None, ProjectIdeaStatus.new)
    assert result.promoted_project_id is None
    assert db.commit_count == 1

    audits = [obj for obj in db.added if isinstance(obj, AuditEvent)]
    assert [a.action for a in audits] == ["project_idea.create"]
    assert audits[0].actor_user_id == user.id


@pytest.mark.asyncio
async def test_patch_project_idea_updates_text_and_archives() -> None:
    from daedalus.api.routes.project_ideas import patch_project_idea
    from daedalus.api.schemas import ProjectIdeaPatch
    from daedalus.db.models import ProjectIdeaStatus

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id)
    db = _FakeSession(project_ideas=[idea])

    await patch_project_idea(
        iid=idea.id,
        body=ProjectIdeaPatch(text="rewritten body", status=ProjectIdeaStatus.archived),
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert idea.text == "rewritten body"
    assert idea.status == ProjectIdeaStatus.archived
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_patch_project_idea_rejects_status_promoted() -> None:
    """Promotion has its own endpoint — the PATCH surface must not flip the bit."""
    from daedalus.api.routes.project_ideas import patch_project_idea
    from daedalus.api.schemas import ProjectIdeaPatch
    from daedalus.db.models import ProjectIdeaStatus

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id)
    db = _FakeSession(project_ideas=[idea])

    with pytest.raises(HTTPException) as excinfo:
        await patch_project_idea(
            iid=idea.id,
            body=ProjectIdeaPatch(status=ProjectIdeaStatus.promoted),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 400
    assert db.commit_count == 0


@pytest.mark.asyncio
async def test_patch_project_idea_returns_409_after_promotion() -> None:
    from daedalus.api.routes.project_ideas import patch_project_idea
    from daedalus.api.schemas import ProjectIdeaPatch

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id, status_value="promoted")
    db = _FakeSession(project_ideas=[idea])

    with pytest.raises(HTTPException) as excinfo:
        await patch_project_idea(
            iid=idea.id,
            body=ProjectIdeaPatch(text="too late"),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 409
    assert idea.text == "hack on a markdown indexer cli"


@pytest.mark.asyncio
async def test_patch_project_idea_returns_403_for_non_owner() -> None:
    from daedalus.api.routes.project_ideas import patch_project_idea
    from daedalus.api.schemas import ProjectIdeaPatch

    owner = _make_user()
    intruder = _make_user()
    idea = _make_project_idea(owner_id=owner.id)
    db = _FakeSession(project_ideas=[idea])

    with pytest.raises(HTTPException) as excinfo:
        await patch_project_idea(
            iid=idea.id,
            body=ProjectIdeaPatch(text="hostile edit"),
            request=_fake_request(),
            user=intruder,
            db=db,  # type: ignore[arg-type]
        )

    assert excinfo.value.status_code == 403
    assert idea.text == "hack on a markdown indexer cli"


@pytest.mark.asyncio
async def test_delete_project_idea_removes_and_audits() -> None:
    from daedalus.api.routes.project_ideas import delete_project_idea
    from daedalus.db.models import AuditEvent

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id)
    db = _FakeSession(project_ideas=[idea])

    await delete_project_idea(
        iid=idea.id,
        request=_fake_request(),
        user=user,
        db=db,  # type: ignore[arg-type]
    )

    assert idea in db.deleted
    assert any(
        isinstance(o, AuditEvent) and o.action == "project_idea.delete"
        for o in db.added
    )
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_promote_creates_project_and_flips_status() -> None:
    """The happy path: idea → project, status flips to `promoted`, FK is set."""
    from daedalus.api.routes.project_ideas import promote_project_idea
    from daedalus.api.schemas import ProjectIdeaPromote
    from daedalus.db.models import AuditEvent, Project, ProjectIdeaStatus

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id)
    db = _FakeSession(project_ideas=[idea])

    # Bypass the workspaces-root canonicalisation so the test isn't tied to
    # an env var. The route only forwards the result onto the Project row.
    with mock_patch(
        "daedalus.api.routes.project_ideas._canonicalize_workspace",
        return_value="/workspaces/markdown-indexer",
    ):
        result = await promote_project_idea(
            iid=idea.id,
            body=ProjectIdeaPromote(
                name="Markdown Indexer",
                description="a tiny cli for indexing markdown",
                workspace_path="/workspaces/markdown-indexer",
                init_git=False,
            ),
            request=_fake_request(),
            user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert isinstance(result, Project)
    assert result.name == "Markdown Indexer"
    assert result.workspace_path == "/workspaces/markdown-indexer"
    assert result.owner_id == user.id

    assert idea.status == ProjectIdeaStatus.promoted
    assert idea.promoted_project_id == result.id
    assert db.commit_count == 1

    audit_actions = [
        o.action for o in db.added if isinstance(o, AuditEvent)
    ]
    assert "project_idea.promote" in audit_actions
    assert "project.create" in audit_actions


@pytest.mark.asyncio
async def test_promote_rejects_already_promoted_idea() -> None:
    from daedalus.api.routes.project_ideas import promote_project_idea
    from daedalus.api.schemas import ProjectIdeaPromote

    user = _make_user()
    idea = _make_project_idea(owner_id=user.id, status_value="promoted")
    db = _FakeSession(project_ideas=[idea])

    with mock_patch(
        "daedalus.api.routes.project_ideas._canonicalize_workspace",
        return_value="/workspaces/x",
    ):
        with pytest.raises(HTTPException) as excinfo:
            await promote_project_idea(
                iid=idea.id,
                body=ProjectIdeaPromote(
                    name="x", workspace_path="/workspaces/x", init_git=False
                ),
                request=_fake_request(),
                user=user,
                db=db,  # type: ignore[arg-type]
            )

    assert excinfo.value.status_code == 409
    assert db.commit_count == 0
