# 08 — Observability, Analytics, CLI & Deployment/Ops

Source: `backend/daedalus/observability.py`, `core/`, `pythia/`, `cli/`, `deploy/`, `Makefile`.

## Observability & analytics

- **Structured JSON logging** via structlog (`core/logging.py`).
- **Prometheus metrics** (`observability.py`): `daedalus_queue_depth`, `daedalus_runs_total`,
  `daedalus_runs_completed_total`, `daedalus_run_duration_seconds` (histogram),
  `daedalus_argus_verdicts_total`, `daedalus_auth_login_total`. Exposed on `/metrics`.
- **KPI endpoint** `GET /api/v1/kpis/projects/{pid}/task-status-timeseries?days=1-365` —
  daily per-status counts via lateral DISTINCT-ON (`kpis.py`).
- **System endpoints**: `GET /api/v1/system/subscription` (Pythia snapshot),
  `GET /api/v1/system/runners` (active runs).
- Optional observability stack (`make obs.up`): Prometheus + Grafana + Loki + Promtail +
  OTel collector + Mailpit.

## Pythia — subscription oracle

- Probes Claude OAuth (`~/.claude/.credentials.json`) → `/api/oauth/profile` + `/usage`:
  plan tier, 5-hour + weekly utilization %, reset countdowns (`pythia/probe.py`).
- Redis-cached; CLI fallback (`claude --print '/status'`); kinds: ok/auth_required/
  cli_missing/timeout/unparsed/error.

## Management CLI (`python -m daedalus.cli`)

- `init` — owner account + TOTP provisioning URI + recovery codes (once).
- `import-connectors` — upsert `.json` specs from dir (also `make seed-connectors`).
- `mint-client-cert --email [--pin]` — issue mTLS cert (`.key`/`.crt`/`.p12`).
- `reset-totp --email [--keep-recovery]` — re-issue TOTP + recovery, clear lockout,
  revoke sessions.
- `reverify-stuck-tasks` — re-run Argus on `needs_fixes` tasks (dry-run or apply).
- All operations audited.

## Deployment topology

- **Three networks**: `frontnet` (Caddy↔API/Iris), `backnet` (API↔workers/DB/Redis/MinIO),
  `agentnet` (Talos/Argus, egress-filtered).
- **Services**: postgres:16, redis:7 (AOF), minio, api, iris, hermes, talos, argus-worker,
  caddy, frontend (nginx), litellm (always-on), llm/vllm (`--profile llm`),
  observability stack (`--profile observability`), mailpit (`--profile dev`),
  agentnet-firewall, pg-backup.
- Persistent volumes for postgres/redis/objects/caddy/prometheus/loki/grafana/hf-cache.

## Caddy mTLS reverse proxy

- Internal CA (`tls internal`), three vhosts on 443 (ports 9443/9080).
- Routes `/ws/*` → Iris, `/api/*` → API, `/` → frontend SPA.
- Security headers (HSTS, X-Frame-Options DENY, nosniff, Referrer-Policy, CSP), gzip/zstd.
- Forwards `X-Client-Cert-Fingerprint` to API.

## Agentnet egress firewall

- Host-network sidecar (`NET_ADMIN`); every `AGENTNET_FIREWALL_RELOAD_SECONDS` reads
  connector `egress_allowlist` ∪ `AGENTNET_FIREWALL_BASELINE_HOSTS`, resolves hosts, and
  rewrites a fenced block in the host `DOCKER-USER` chain (DNS + ESTABLISHED + one ACCEPT
  per IP, REJECT rest). Emergency bypass documented (`deploy/agentnet.md`).

## Postgres backups

- pg-backup sidecar: `pg_dump → gzip → mc cp` to MinIO on boot + on `PG_BACKUP_SCHEDULE`
  (default `30 2 * * *` UTC); prunes older than `PG_BACKUP_RETENTION_DAYS`.
- `make backup.now / backup.list / backup.restore KEY=…`.

## Makefile operator targets

`up/down/restart/logs/ps/build/pull`, `backend.dev/shell/test/lint`, `migrate/revision`,
`frontend.dev/build`, `obs.up/down`, `llm.up/down/logs/pull/models`, `init`,
`seed-connectors`, `reset-totp`, `mint-cert`, `backup.*`, `clean`.

## Core configuration

`ROLE` (api/iris/hermes/talos/argus), `DATABASE_URL`, `REDIS_URL`, `S3_*`,
`SESSION_SECRET`, `PASSWORD_PEPPER`, `max_concurrent_projects` (4),
`scheduler_poll_seconds` (0.5), `project_lease_grace_seconds` (300),
`talos_shutdown_drain_seconds` (45), `TALOS_WORKSPACES_ROOT` (/workspaces),
`TALOS_CONNECTORS_DIR`, `DAEDALUS_PUBLIC_URL`, `INTERNAL_API_*`, `LLM_*`, `SMTP_*`.
