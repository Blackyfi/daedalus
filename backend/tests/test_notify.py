"""Outbound notification gating + payload (IMPROVEMENTS #17)."""
from __future__ import annotations

import daedalus.notify as notify
from daedalus.core.settings import Settings


def _settings(url=None, events="needs_fixes,anomaly"):
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://x/x",
        redis_url="redis://x",
        session_secret="s",
        password_pepper="p",
        notify_webhook_url=url,
        notify_events=events,
    )


def test_disabled_without_webhook(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(url=None))
    assert notify.should_notify("anomaly") is False


def test_event_must_be_opted_in(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(url="https://h", events="anomaly"))
    assert notify.should_notify("anomaly") is True
    assert notify.should_notify("needs_fixes") is False


def test_empty_events_means_all(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(url="https://h", events=""))
    assert notify.should_notify("anything") is True


def test_emit_is_noop_and_never_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings(url=None))
    # No running loop, disabled -> returns cleanly, no exception.
    notify.emit("anomaly", "test")


def test_build_payload_shape():
    body = notify.build_payload("needs_fixes", "Task X failed", {"task_id": "abc"})
    assert body["source"] == "daedalus"
    assert body["event"] == "needs_fixes"
    assert body["summary"] == "Task X failed"
    assert body["task_id"] == "abc"
    assert "at" in body
