"""Coverage for the login step-gating (C1) and per-step throttle (C2).

The three login steps are separate endpoints; without a server-side stage
marker, /totp would be a standalone login and the 6-digit code brute-forceable.
These tests exercise the gate helpers and the failure-lockout helper directly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from daedalus.api.routes import auth as auth_routes
from daedalus.core.settings import get_settings


class _FakeRedis:
    """Minimal async stand-in supporting set(ex=)/get/delete/incr/expire."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value.encode() if isinstance(value, str) else value

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def incr(self, key: str) -> int:
        raw = self.store.get(key)
        cur = int(raw.decode()) if raw else 0
        cur += 1
        self.store[key] = str(cur).encode()
        return cur

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl


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


def test_coerce_count_handles_bytes_int_and_none() -> None:
    assert auth_routes._coerce_count(None) == 0
    assert auth_routes._coerce_count(b"7") == 7
    assert auth_routes._coerce_count(7) == 7
    assert auth_routes._coerce_count(b"") == 0
    assert auth_routes._coerce_count(b"garbage") == 0


@pytest.mark.asyncio
async def test_ip_failures_increment_and_trip_at_threshold(fake_redis) -> None:
    threshold = get_settings().ip_ban_threshold
    ip = "203.0.113.7"
    assert await auth_routes._ip_throttled(ip) is False
    for _ in range(threshold - 1):
        await auth_routes._register_ip_failure(ip)
    assert await auth_routes._ip_throttled(ip) is False  # one below
    await auth_routes._register_ip_failure(ip)            # hits threshold
    assert await auth_routes._ip_throttled(ip) is True
    # first failure set an expiry window
    assert fake_redis.ttls.get(f"{auth_routes._IP_FAIL_PREFIX}:{ip}") == get_settings().ip_ban_minutes * 60


@pytest.mark.asyncio
async def test_ip_clear_resets_throttle(fake_redis) -> None:
    ip = "203.0.113.8"
    for _ in range(get_settings().ip_ban_threshold):
        await auth_routes._register_ip_failure(ip)
    assert await auth_routes._ip_throttled(ip) is True
    await auth_routes._clear_ip_failures(ip)
    assert await auth_routes._ip_throttled(ip) is False


@pytest.mark.asyncio
async def test_ip_throttle_noop_without_ip(fake_redis) -> None:
    assert await auth_routes._ip_throttled(None) is False
    await auth_routes._register_ip_failure(None)  # must not raise
    await auth_routes._clear_ip_failures(None)


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
    assert user.locked_until > datetime.now(UTC) + timedelta(minutes=14)
