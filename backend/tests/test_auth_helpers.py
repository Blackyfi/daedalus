import pytest

from daedalus.auth.policy import policy_violations
from daedalus.core.settings import get_settings


def test_password_policy_requires_length_and_classes() -> None:
    problems = policy_violations("short")

    assert any("at least" in problem for problem in problems)
    assert any("lowercase" in problem for problem in problems)


def test_recovery_code_consumption_removes_matching_hash(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("PASSWORD_PEPPER", "test-pepper")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SESSION_SECRET", "secret")

    pytest.importorskip("pyotp")

    from daedalus.auth.totp import consume_recovery_code, hash_recovery_code

    code = "abcd-ef01-2345"
    stored = [hash_recovery_code(code), hash_recovery_code("ffff-eeee-dddd")]

    ok, remaining = consume_recovery_code(stored, code)

    assert ok is True
    assert len(remaining) == 1
    get_settings.cache_clear()
