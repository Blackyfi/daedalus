# Testing with the 3FA-bypass stack

Daedalus's production login is deliberately heavy: mTLS + password + email OTP +
TOTP/passkey. That's correct for the live deployment but makes it impossible to
drive the UI end-to-end (Playwright, manual QA) without a real inbox and an
authenticator. The **isolated test stack** removes that friction so the UI can be
exercised with zero assumptions — while keeping the live deployment untouched.

## TL;DR

```bash
make test.up        # build + start the isolated stack (3FA bypass ON)
make test.url       # prints https://localhost:9543
# open the URL → Login page shows a yellow "🧪 Test login (skip 3FA)" button → click it
make test.e2e       # or run Playwright against it
make test.down      # tear it down AND drop its volumes (disposable DB)
```

## What it is

- A second Compose **project** (`-p daedalus-test`) layering
  `deploy/docker-compose.test.yml` over the normal compose file. Separate
  containers, **separate volumes**, separate networks — it never collides with
  the live `deploy-*` stack.
- Published on **alt ports** (9543/9180 by default; override with
  `TEST_HTTPS_PORT` / `TEST_HTTP_PORT`) so the live 9443/9080 stay free.
- The override sets two env vars on the `api` service:
  - `TEST_AUTH_BYPASS_ENABLED=true` — exposes `POST /api/v1/auth/test-login`.
  - `REQUIRE_CLIENT_CERT=false` — no mTLS at the proxy on the test stack.
- The DB starts empty; migrations auto-apply on boot; the first test-login
  **find-or-creates an owner** (`owner@daedalus.test`), so a fresh stack is
  immediately drivable.

`make test.up` brings up only the core services (caddy, api, iris, frontend,
postgres, redis, minio) — enough to drive every page, with **no agent workers**,
so it spends no Claude quota and needs no credentials. `make test.up.full` adds
the run pipeline (hermes/talos/argus/litellm) for end-to-end agent runs; that one
needs your `~/.claude` creds mounted and **does** spend quota.

## The bypass

- **Endpoint:** `POST /api/v1/auth/test-login` with optional `{ "email": "..." }`.
  Mints a normal session cookie with **no factors**. Returns the user.
- **`GET /api/v1/auth/status`** reports `test_bypass: true` on this stack; the
  SPA login page reads that to render the bypass button.

## Why this is safe for production

Defence in depth — all three must independently fail for this to leak:

1. **Default off.** `Settings.test_auth_bypass_enabled` defaults `False`.
2. **Structural fence.** The production compose's `x-common-env` block does
   **not** forward `TEST_AUTH_BYPASS_ENABLED`, so even if it were set in `.env`
   the live containers would never see it. Only `docker-compose.test.yml` wires
   it in.
3. **Invisible when off.** The endpoint returns **404** (not 403) unless the
   flag is on, so it doesn't betray its own existence on the live stack.

Every test-login is written to the audit log as `auth.test_login`.

## CI

The hermetic guard tests (`backend/tests/test_test_login_bypass.py`) assert the
flag defaults off, the endpoint 404s when disabled, and `/status` reflects the
flag. They run in the normal `make ci` gate. The session-minting happy path is
proven against this live test stack.
