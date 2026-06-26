"""The TEST-ONLY auth bypass (POST /api/v1/auth/test-login).

Security-critical: this endpoint defeats 3FA, so it must be invisible (404, not
403) and inert whenever the flag is off, and the flag must default off. The full
session-minting happy path runs against the isolated test stack (it needs a real
Postgres for the User/Session rows); here we pin the guard + default behaviour.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from daedalus.api.routes import auth as auth_routes
from daedalus.core.settings import Settings


def test_flag_defaults_off() -> None:
    # _env_file=None so a developer's real .env can't accidentally flip it on in
    # the suite; required fields come from conftest's os.environ defaults.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.test_auth_bypass_enabled is False


@pytest.mark.asyncio
async def test_test_login_404_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_routes, "get_settings",
        lambda: SimpleNamespace(test_auth_bypass_enabled=False),
    )
    with pytest.raises(HTTPException) as ei:
        await auth_routes.test_login(
            body=auth_routes.TestLoginIn(),
            request=SimpleNamespace(headers={}, client=None),
            response=SimpleNamespace(),
            cert_fp=None,
            db=None,  # never reached — the flag check precedes any DB access
        )
    # 404, not 403: the route must not betray its own existence when disabled.
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_status_exposes_bypass_flag(monkeypatch) -> None:
    for enabled in (True, False):
        monkeypatch.setattr(
            auth_routes, "get_settings",
            lambda enabled=enabled: SimpleNamespace(
                test_auth_bypass_enabled=enabled,
                require_client_cert=False,
                session_hard_hours=12,
            ),
        )
        # No cookie → early return before any DB access; lets the login page
        # learn about the bypass affordance pre-session.
        out = await auth_routes.status_probe(cert_fp=None, session_cookie=None, db=None)
        assert out["authenticated"] is False
        assert out["test_bypass"] is enabled
