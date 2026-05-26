"""Coverage for the login step-gating (C1) and per-step throttle (C2).

The three login steps are separate endpoints; without a server-side stage
marker, /totp would be a standalone login and the 6-digit code brute-forceable.
These tests exercise the gate helpers and the failure-lockout helper directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from daedalus.api.routes import auth as auth_routes


class _FakeRedis:
    """Minimal async stand-in supporting set(ex=)/get/delete."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value.encode() if isinstance(value, str) else value

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    r = _FakeRedis()
    monkeypatch.setattr(auth_routes, "get_redis", lambda: r)
    return r


@pytest.mark.asyncio
async def test_stage_absent_by_default(fake_redis) -> None:
    assert await auth_routes._get_login_stage("a@b.com") is None


@pytest.mark.asyncio
async def test_password_step_sets_otp_stage_not_totp(fake_redis) -> None:
    await auth_routes._set_login_stage("A@B.com", auth_routes._LOGIN_STAGE_OTP)
    # email is normalised to lower-case in the key
    assert await auth_routes._get_login_stage("a@b.com") == auth_routes._LOGIN_STAGE_OTP
    # crucially: a fresh password step does NOT satisfy the /totp gate
    assert await auth_routes._get_login_stage("a@b.com") != auth_routes._LOGIN_STAGE_TOTP


@pytest.mark.asyncio
async def test_otp_step_advances_to_totp(fake_redis) -> None:
    await auth_routes._set_login_stage("u@x.com", auth_routes._LOGIN_STAGE_OTP)
    await auth_routes._set_login_stage(
        "u@x.com", auth_routes._LOGIN_STAGE_TOTP, ttl=auth_routes._LOGIN_STAGE_TOTP_TTL
    )
    assert await auth_routes._get_login_stage("u@x.com") == auth_routes._LOGIN_STAGE_TOTP


@pytest.mark.asyncio
async def test_totp_success_clears_stage(fake_redis) -> None:
    await auth_routes._set_login_stage("u@x.com", auth_routes._LOGIN_STAGE_TOTP)
    await auth_routes._clear_login_stage("u@x.com")
    assert await auth_routes._get_login_stage("u@x.com") is None


def test_register_auth_failure_increments_then_locks() -> None:
    settings = SimpleNamespace(lockout_threshold=3, lockout_minutes=15)
    user = SimpleNamespace(failed_login_count=0, locked_until=None)

    auth_routes._register_auth_failure(user, settings)
    assert user.failed_login_count == 1 and user.locked_until is None

    auth_routes._register_auth_failure(user, settings)
    assert user.failed_login_count == 2 and user.locked_until is None

    auth_routes._register_auth_failure(user, settings)
    assert user.failed_login_count == 3
    assert user.locked_until is not None
    assert user.locked_until > datetime.now(timezone.utc) + timedelta(minutes=14)
