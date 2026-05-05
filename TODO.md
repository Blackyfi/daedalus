# Daedalus — Outstanding Work

Snapshot taken **2026-05-03**, after the high-value-gaps session
(token/cost parser, multi-attach PTY hand-off, transcript diff viewer,
`reset-totp` CLI) and the operational-gaps session (Postgres backup
sidecar, agentnet egress firewall, prod extras toggle, mint-client-cert
CLI). References point at files in this repo and section numbers in
`project-plan.md`.

The platform is feature-complete and operationally complete against
the v1 spec. Everything below is an explicit deferral or a v1.x
nice-to-have.

---

## Deferred by design

- [ ] **Connector signing (spec §10.6).** Skipped — single-org
  self-hosted, only owners upload connectors. Reconsider if multi-admin
  support ever lands.

## Nice-to-haves

- [ ] **Per-project token spend cap UI.** Parser + persistence shipped;
  the cap field on `Project` and the UI to set/enforce it are still
  TODO.
- [ ] **Connector hot-reload UI button.** Server-side hot-reload works
  (§7.4); the SPA has no button to trigger it or display reload status.
- [ ] **Audit-log anomaly detection (spec §15 phase 6).** Phase-6
  hardening.
- [x] **vLLM container + `--profile llm`** — new `llm` service in
  `docker-compose.yml` (image `vllm/vllm-openai:v0.6.4.post1`,
  `network_mode: backnet`, GPU passthrough via
  `deploy.resources.reservations.devices`, `ipc: host`, named volume
  `daedalus_hf_cache` for the HuggingFace cache, 600 s health-check
  start period to absorb cold model loads). Reuses the existing
  `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` config — no platform
  code change. New `VLLM_*` knobs (image tag, GPU count, tensor
  parallel, `max_model_len`, `gpu_memory_utilization`, `dtype`,
  `extra_args`, log level, `HF_TOKEN`) documented in `.env.example`.
  Make targets: `llm.up / llm.down / llm.logs / llm.pull / llm.models`.
  README documents the profile, prerequisites (NVIDIA Container
  Toolkit), and stacking with `--profile dev / observability`.

---

## Just done — operational session

- [x] **Postgres backup sidecar (spec §14.3).** New
  `deploy/pg-backup/` (Dockerfile + `entrypoint.sh` + `backup.sh`)
  builds on `postgres:16-alpine` with the MinIO `mc` client baked in.
  Runs an initial `pg_dump → gzip → mc cp` on boot, then sleeps until
  the next firing of `PG_BACKUP_SCHEDULE` (default `30 2 * * *` UTC).
  Old dumps in `${PG_BACKUP_BUCKET}/${POSTGRES_DB}/` are pruned via
  `mc rm --older-than ${PG_BACKUP_RETENTION_DAYS}d`. Wired into compose
  on `backnet` with healthcheck dependencies on postgres+minio.
  `make backup.now / backup.list / backup.restore KEY=…` for ops.
- [x] **Automated agentnet egress filter (spec §10.8).** New
  `deploy/agentnet-firewall/` sidecar runs in `network_mode: host`
  with `cap_add: [NET_ADMIN]`. Every
  `AGENTNET_FIREWALL_RELOAD_SECONDS` it reads every connector spec
  under `connectors/`, builds the union of `egress_allowlist` plus the
  `AGENTNET_FIREWALL_BASELINE_HOSTS` env var, resolves each host, and
  rewrites a fenced (`daedalus-agentnet-{start,end}` comment markers)
  block in the host's `DOCKER-USER` chain — DNS + RELATED/ESTABLISHED +
  one ACCEPT per resolved IP, then a REJECT for the rest. `deploy/
  agentnet.md` is rewritten to document the automation; the manual
  recipe is kept as an emergency fallback.
- [x] **Prod image excludes `[dev]` extras.** Dockerfile already
  defaults `DAEDALUS_INSTALL_EXTRAS=""`; compose now forwards the build
  arg through every backend service, and `.env.example` documents the
  toggle (set `DAEDALUS_INSTALL_EXTRAS=dev` to rebuild with test deps).
- [x] **mTLS client-cert helper.** New
  `daedalus/auth/client_certs.py` mints a 4096-bit RSA key, signs a
  client cert against the internal CA (RSA-only assumption, KeyUsage =
  digitalSignature/keyEncipherment, EKU = clientAuth, SAN with the
  email), and writes the `.key` / `.crt` / `.p12` trio with `0600`.
  New `daedalus.cli mint-client-cert --email ... [--pin]` (also
  `make mint-cert EMAIL=… PIN=true`). With `--pin`, the SHA-256
  fingerprint is written to `User.pinned_cert_fingerprint` so login
  cookies bind to that specific cert (§10.2), and an
  `auth.client_cert_minted` audit event is emitted.

---

## Just done — high-value session

- [x] **Token / cost tracking.** New `daedalus/connectors/usage.py`
  parses `claude` / `openai` / `regex` / `json_block` shapes from the
  transcript at run completion (Talos), ships counts in the completion
  payload, and the Hermes scheduler persists them onto
  `runs.token_input` / `token_output` / `cost_usd_micros`. Connector
  schema gains a `usage_parser` block; bundled Claude/Codex/Qwen specs
  use it. Surfaced in `RunOut` and the SPA `RunPanel` header.
- [x] **Multi-attach PTY hand-off (spec §11.2).** Iris's
  `/ws/pty/{run_id}` now speaks a JSON envelope (`data` / `state` /
  `input` / `takeover` / `release` / `ping`). Holder state lives at
  `pty:holder:{rid}` in Redis with a 120 s TTL refreshed by holder
  activity, and changes broadcast on `pty:state:{rid}` so every attached
  client re-reads the holder. First connection auto-claims; non-holder
  input is dropped. The SPA's `RunPanel` shows an `Input: …` badge, a
  `Take input` / `Release input` button, and toasts the previous holder
  when ownership flips.
- [x] **Transcript diff viewer (spec §8.6, §11.4).** New
  `GET /api/v1/runs/{rid}/diff` endpoint returns
  `git diff <default_branch>...HEAD --no-color` from the run's worktree
  (uses cached `Run.diff_object_key` if present). New `DiffViewer.tsx`
  parses unified diffs with `parsePatch` and renders side-by-side rows
  with line numbers + add/remove highlighting; opened from the
  RunPanel's `diff` button.
- [x] **`daedalus reset-totp` offline CLI (spec §17).** New
  `python -m daedalus.cli reset-totp --email <addr>` (also
  `make reset-totp EMAIL=…`) re-issues the TOTP secret, regenerates
  recovery codes (skip with `--keep-recovery`), revokes any active
  sessions, and emits an `auth.totp_reset_offline` audit event.
  Provisioning URI + new recovery codes print to stdout.

---

## Done in earlier sessions (kept for context)

### Critical (core promise)

- [x] **Argus is an LLM verifier (spec §6.4).**
  `daedalus/argus/verifier.py` builds a structured prompt and asks the
  configured LLM for `{verdict, summary, findings, suggested_fix_task}`;
  scheduler persists it. Deterministic fallback if the LLM is
  unreachable.
- [x] **Planning is an LLM call (spec §6.4, §8.4).**
  `daedalus/planning/planner.py` reads repo tree + README + tasks +
  connectors + ideas and returns a dependency-ordered proposal.
  Falls back to the 1-idea-1-task transform offline.
- [x] **Resource limits enforced via cgroups v2.** New
  `daedalus/talos/cgroups.py` creates
  `/sys/fs/cgroup/daedalus.slice/run-<id>/` and applies `cpu.weight`,
  `memory.max`, `pids.max`. No-ops on cgroup v1 / macOS.
- [x] **Done-signal kinds**: `regex`, `exit_code` (warns on mismatch),
  and `tool_call` (JSON `"name":"<tool>"` or `<<TASK_DONE:<tool>>>`)
  all implemented in `talos/runner.py:_wait_for_completion`.

### Auth / security

- [x] **WebAuthn / hardware-key (spec §10.2 v1.1).** Full flow in
  `auth/webauthn_svc.py` + `api/routes/webauthn.py`. SPA Security page
  enrolls keys; LoginPage offers "Use a hardware key" once enrolled.
- [x] **Yolo rollback in the UI.** Rollback button shown in
  `RunPanel.tsx` when a snapshot exists.
- [x] **Audit-log UI (spec §10.7).** `pages/AuditPage.tsx` with
  sortable, refreshing table.
- [x] **`Connector.enabled` flag.** List endpoint filters disabled
  connectors by default; `_connector_snapshot_for_run` raises HTTP 409
  if a disabled connector is referenced. New
  `POST /api/v1/connectors/{cid}/{enable,disable}`.
- [x] **Per-connector `egress_allowlist` (spec §10.8).** Connector-spec
  field added with a JSON-Schema entry; Talos logs the allowlist on
  each run; the new `agentnet-firewall` sidecar enforces it.

### Frontend

- [x] **Real React+TS+Vite SPA** in `frontend/` with Tailwind, TanStack
  Query, Zustand, `react-router-dom`. Containerised behind nginx and
  served via Caddy alongside the API.
  - Pages: Login (3-step + WebAuthn shortcut), ProjectList, Project
    (kanban + Plan Review + Idea Box + RunPanel), Connectors, Audit,
    Security.
  - Components: `TaskBoard` (6 columns), `IdeaBox`, `PlanReview`,
    `RunPanel`, `DiffViewer`.
- [x] **xterm.js** wired in `RunPanel` with FitAddon; PTY resize is
  end-to-end (REST + Hermes signal + Talos handler + xterm
  `onResize`).
- [x] **Plan Review modal (spec §8.4).** Per-field editing of title /
  description / acceptance criteria / priority / suggested connector,
  per-task remove and confirm-all.

### Bugs fixed

- [x] Talos `_handle_signal` for `kill` no longer double-completes —
  `_complete_run` is idempotent via `_completion_published`.
- [x] Unused imports in `talos/runner.py` removed.
- [x] `runs.py:list_runs_for_project` — `user` dependency moved before
  the optional `limit`.
- [x] Dockerfile installs are driven by `DAEDALUS_INSTALL_EXTRAS`
  (default no extras).
- [x] `talos/__main__.py` SIGTERM handler now calls
  `runner.request_shutdown()`; clean drain instead of `sys.exit(0)`.
- [x] `talos/runner.py:_release_lock` actually `DEL`s
  `hermes:lock:{run_id}` in `_execute_task` finally.
- [x] Lifecycle signals arriving before Talos has the run active are
  buffered in `hermes:pending_signals:{run_id}` (10-min TTL) and
  replayed during `_execute_task`.
- [x] Caddyfile route ordering: `@ws`, `@api`, and a
  `@spa not path /ws/* /api/*` matcher make the three reverse_proxy
  targets explicit.
- [x] `iris/main.py` and `db/base.py` no longer duplicate
  `_engine`/`_sessionmaker`; iris uses `db/base.py`'s helpers.
- [x] Iris starts under `python -m daedalus.iris.main`.
- [x] Hermes scheduler's planning callback uses `INTERNAL_API_BASE`
  (default `http://api:8000`) instead of the public mTLS URL.
- [x] Caddy publishes on `${DAEDALUS_HTTPS_PORT:-9443}` /
  `${DAEDALUS_HTTP_PORT:-9080}` to avoid common-port conflicts.

### Operational

- [x] **SMTP relay container** — `mailpit` under the `dev` compose
  profile; web UI on `${DAEDALUS_MAILPIT_UI_PORT:-9025}`.
- [x] **Observability stack** — Prometheus + Loki + Promtail + OTel
  Collector + Grafana under the `observability` profile.
  Pre-provisioned datasources + Daedalus overview dashboard. API
  exposes `/metrics`; Hermes instruments queue depth, run duration,
  completions, and Argus verdicts.
- [x] **API container auto-runs migrations** via `entrypoint.sh`
  (`alembic upgrade head` then uvicorn). Set
  `DAEDALUS_AUTO_MIGRATE=false` to skip. Other roles
  (iris/hermes/talos/argus) dispatched from the same script.
- [x] **No CI** — confirmed skipped per user direction (no remote
  runner; `make backend.test` + `make backend.lint` for local).
