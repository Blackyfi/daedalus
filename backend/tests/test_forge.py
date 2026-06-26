"""Forge PR payload + enablement gating (IMPROVEMENTS #7)."""
from __future__ import annotations

import pytest

import daedalus.forge.client as forge
from daedalus.core.settings import Settings


def _settings(**kw):
    base = dict(
        _env_file=None,
        database_url="postgresql+asyncpg://x/x",
        redis_url="redis://x",
        session_secret="s",
        password_pepper="p",
    )
    base.update(kw)
    return Settings(**base)


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(forge, "get_settings", lambda: _settings())
    assert forge.forge_enabled() is False


def test_enabled_when_fully_configured(monkeypatch):
    monkeypatch.setattr(
        forge,
        "get_settings",
        lambda: _settings(forge_provider="github", forge_token="t", forge_repo="o/r"),
    )
    assert forge.forge_enabled() is True


def test_github_payload():
    p = forge.build_pr_payload("github", head="feat", base="main", title="T", body="B")
    assert p == {"title": "T", "head": "feat", "base": "main", "body": "B"}


def test_gitlab_payload():
    p = forge.build_pr_payload("gitlab", head="feat", base="main", title="T", body="B")
    assert p["source_branch"] == "feat" and p["target_branch"] == "main"


def test_unsupported_provider():
    with pytest.raises(forge.ForgeError):
        forge.build_pr_payload("bitbucket", head="a", base="b", title="t", body="x")
