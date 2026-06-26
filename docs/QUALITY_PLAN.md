# Daedalus — Quality, Testing, SOTA-Benchmark & Improvement Plan

> Goal: for **every feature** in [`features/`](./features/), (1) verify it actually works
> with **real evidence**, (2) judge it against **great UX** and the **current state of the
> art**, and (3) produce a prioritized backlog of **new features and upgrades**.

This plan is executed by fanning out subagents. Findings land in [`evidence/`](./evidence/)
(raw logs, screenshots, transcripts), assessments in [`assessments/`](./assessments/), and
the consolidated backlog in [`IMPROVEMENTS.md`](./IMPROVEMENTS.md).

---

## 0. Principles & evidence standard

- **No claim without evidence.** Every "works/broken" verdict cites a real artifact:
  an HTTP response, a pytest line, a transcript, a screenshot, a DB/Redis read.
- **Test against the live stack** (`deploy-*` containers, Caddy `:9443`) and a **mock repo**,
  never against real user projects, and **never ship to a real `main`**.
- **Mind the stale-image gotcha.** Running containers are baked images (no source
  bind-mount). To exercise *working-tree* backend code against the live DB/Redis, run a
  one-off container from the `deploy-hermes` image with `backend/` mounted over `/app`, on
  `deploy_backnet`, `--env-file .env` (pattern proven in prior E2E).
- **Always clean up.** Delete throwaway users/projects/runs; clear Redis keys
  (`auth:ip_fail:*`, anomaly cooldowns); leave the DB as found (except deliberately
  preserved evidence rows, documented in the evidence log).

### Test environment

| Resource | How |
|----------|-----|
| Live API | `https://localhost:9443/api/...` (cert not required — `REQUIRE_CLIENT_CERT=false`) |
| Health | `GET /api/health` → `{"status":"ok"}` (verified) |
| Auth (test) | throwaway owner via `docker exec deploy-api-1 python -m daedalus.cli init --email x@daedalus-qa.dev --password ... --role owner`; capture TOTP secret. Reject `.local/.test/.example` TLDs. |
| OTP bypass | overwrite latest `EmailOTP.code_hash` with `daedalus.auth.email_otp._hmac("12345678")`, then submit `12345678`; or read code from Mailpit (`:9025`) if SMTP points there |
| TOTP | `pyotp.TOTP(secret).now()` |
| Mock repo | fresh git repo under `/workspaces/daedalus-qa-<ts>` with seed commits + a deliberately fixable bug |
| Backend tests | `cd backend && python -m pytest` (21 suites) |
| Frontend build | `cd frontend && npm run build` |

---

## 1. Test methodology per feature

Each feature gets classified and tested at the cheapest level that yields real evidence:

1. **Unit / contract** — pure logic (parsers, policy, cost math, anomaly rules, cgroup
   writers). Run/extend the existing `backend/tests/` suite.
2. **API integration** — hit the endpoint on the live stack, assert status + body shape +
   side effects (DB row, Redis key, audit event).
3. **End-to-end** — drive a real flow through the UI/API: task → enqueue → Hermes claim →
   Talos run → diff/transcript → Argus verdict → fix-loop → merge → ship, on the mock repo.
4. **UI** — render + interaction. **Gap: frontend has zero automated tests today.** Plan
   adds a Playwright (E2E) + Vitest (component) harness; until then, evidence = scripted
   browser screenshots + manual interaction notes.
5. **Failure-mode** — timeouts, LLM-down fallback, rate-limit pause, orphan recovery,
   conflict resolution, rollback, lockout/ban — induced deliberately.

### Coverage matrix (one row per feature area → fill with evidence)

| # | Area | Level | Existing test | Evidence target |
|---|------|-------|---------------|-----------------|
| 1 | Password / policy / Argon2id | unit+API | `test_auth_helpers`, `test_auth_login_stages` | login 3FA walkthrough |
| 2 | Email OTP (code + magic link) | API+E2E | `test_auth_login_stages` | OTP issue→verify log |
| 3 | TOTP + recovery codes | API | partial | pyotp login + recovery-code login |
| 4 | WebAuthn register/auth | API+UI | — *(none)* | SecurityPage enroll, sign-count |
| 5 | mTLS cert mint + pin | unit | `test_client_certs` | mint cert, pin, mismatch reject |
| 6 | Sessions (idle/hard/revoke) | API | partial | idle-timeout re-auth |
| 7 | Lockout + IP ban | API | `test_security_hardening` | induce 5 fails → lock |
| 8 | Audit log + filters | API+UI | — | audit rows for every action |
| 9 | Anomaly detection (4 rules) | unit+integ | `test_anomaly` | live scan fires + cooldown |
| 10 | Project CRUD + settings | API | — | create/patch/archive |
| 11 | Project stats / cycle time | API | — | stats payload on mock data |
| 12 | Git-status + pull guard | API | — | behind-repo enqueue block |
| 13 | Cost cap (402) | unit+API | `test_cost_cap` | cap reached → 402 |
| 14 | Discovery (scan + register) | API | — | scan mock workspaces |
| 15 | Task CRUD / DAG / run-all | API+E2E | — | dependency gating |
| 16 | Ideas / notes CRUD | API | `test_ideas_patch` | — |
| 17 | LLM planning + fallback | unit+API | `test_internal_planning`, `test_plan_proposals` | plan from ideas |
| 18 | Plan review confirm/discard | API+UI | `test_plan_proposals` | confirm → tasks created |
| 19 | Hermes lanes / lease / claim | unit+integ | `test_project_lease` | single-runner proof |
| 20 | Orphan recovery | integ | — | kill mid-run → aborted_unsafe |
| 21 | Rate-limit pause | integ | — | simulate 429 → connector paused |
| 22 | Talos PTY lifecycle (all signals) | E2E | `test_iris_holder` | pause/resume/kill/inject/resize |
| 23 | Wall-clock + idle timeout | integ | — | induce both |
| 24 | Done-signal kinds | unit | — | regex/exit/tool_call |
| 25 | Per-run worktree + auto-commit | E2E | — | worktree + commit on success |
| 26 | cgroup limits | unit | `test_cgroups` | memory.max applied |
| 27 | Argus verdict + findings | E2E | — | partial/fail with findings |
| 28 | No-progress diff-hash halt | integ | — | identical diff halts loop |
| 29 | Phantom-commit guard | integ | — | fake SHA → fail |
| 30 | Fix-loop spawning + depth cap | integ | `test_fix_chain_cap` | chain → manual-review |
| 31 | Run transcript / diff | API+E2E | — | fetch both |
| 32 | Snapshot + rollback | E2E | — | yolo snapshot, rollback resets |
| 33 | Run retry | API | — | failed → new queued run |
| 34 | Merge preview/execute/resolve/ship | E2E | — | full merge of 2 branches w/ conflict |
| 35 | Connector schema validation | unit | `test_connectors` | invalid spec rejected |
| 36 | Connector hot-reload | API | `test_connector_reload` | reload summary |
| 37 | Connector overrides | unit+API | — | force override applied |
| 38 | Usage / cost parser (4 kinds) | unit | `test_usage_parser` | token+cost extraction |
| 39 | LLM client (cli/http, json retry) | unit | `test_llm` | json reprompt |
| 40 | Object store (S3 + fallback) | unit | `test_object_store` | put/get/local fallback |
| 41 | Pythia subscription oracle | unit+API | `test_pythia_parser` | `/system/subscription` |
| 42 | KPI time-series | API+UI | — | timeseries payload + chart |
| 43 | Prometheus metrics | API | — | `/metrics` scrape |
| 44 | CLI (init/cert/totp/reverify) | unit | `test_cli` | each command |
| 45 | Realtime WS (pty/events/queue) | E2E | `test_iris_holder` | live stream + holder handoff |
| 46 | Frontend pages render | UI | — *(none)* | screenshot each page |
| 47 | Caddy routing + headers | integ | — | header assertions |
| 48 | Agentnet firewall | integ | — | egress allow/deny |
| 49 | pg-backup | integ | — | backup.now → MinIO object |

---

## 2. UX evaluation rubric

Score each surface 1–5 on: **discoverability**, **feedback/latency**, **error recovery**,
**information density**, **consistency**, **accessibility**, **mobile**, **safety rails**.
Compare each against "what would be great":

- **Onboarding** — how fast from zero to first verified task? (init CLI is operator-only —
  is there a guided first-run?)
- **The control room** — does the dashboard answer "what needs my attention right now?" in
  one glance? (ProjectActionBar inbox is a strong start.)
- **The live run** — terminal mirroring, input handoff, cost ticker: is intervention
  friction low?
- **Review loop** — plan review, diff viewer, Argus findings, merge conflict resolution:
  is the human-in-the-loop ergonomic?
- **Trust & safety** — are yolo/snapshot/rollback/egress affordances legible and reassuring?
- **Observability** — can a user diagnose a stuck/failed run without `docker logs`?

---

## 3. State-of-the-art benchmark

Compare Daedalus, dimension by dimension, against the 2026 landscape (research via web):

- **Autonomous SWE agents / platforms**: Devin (Cognition), OpenHands (All-Hands),
  SWE-agent, Cursor background/agents, GitHub Copilot Workspace / coding agent,
  Factory, Charlie, Google Jules, Codex/Codex-cloud, Claude Code itself.
- **Local agent orchestrators / "agent managers"**: Conductor, Vibe Kanban, Sculptor,
  Terragon, Crystal, container-use, and similar multi-agent/worktree managers.
- **Verification & eval**: SWE-bench / SWE-bench-Verified methodology, LLM-as-judge
  guardrails, self-consistency, test-generation-before-fix, regression gates.
- **Self-hosted dev-infra UX & security**: Coolify/Dokploy (self-host UX),
  WebAuthn/passkey best practices, mTLS/zero-trust patterns, audit/anomaly tooling.

For each dimension produce: **where Daedalus leads**, **where it's at parity**, **where it
lags**, and **the concrete upgrade** that closes the gap.

---

## 4. Improvement discovery

Output a prioritized backlog ([`IMPROVEMENTS.md`](./IMPROVEMENTS.md)) with, per item:
problem → proposed change → why it's better (UX or SOTA evidence) → effort (S/M/L) →
risk → affected files. Categories:

- **New features** not present today (e.g. test-generation-before-fix, parallel multi-run
  per project, agent-to-agent review, GitHub/GitLab PR integration, cost forecasting,
  run replay/timeline, notifications/webhooks, RBAC beyond owner/member, mobile PWA).
- **Upgrades** to existing features (e.g. richer Argus rubrics, diff review comments,
  streaming plan generation, smarter merge ordering, queue fairness, flaky-verify retries).
- **Quality/test debt** (frontend test harness, E2E CI, contract tests, load tests).

---

## 5. Execution phases & subagent fan-out

| Wave | Agents (parallel) | Output |
|------|-------------------|--------|
| **0 — baseline (safe)** | backend pytest + ruff; frontend build; unauthenticated live probes | `evidence/baseline.md` |
| **1 — SOTA research** | 3 web agents: (a) orchestration platforms, (b) UX + autonomous-coding eval, (c) security/self-hosted/observability | `assessments/sota-*.md` |
| **2 — live E2E (mock repo)** | authed 3FA walkthrough; task→run→Argus→fix→merge→ship on mock repo; failure-mode induction; **with cleanup** | `evidence/e2e-*.md` |
| **3 — UI evidence** | scripted page render + interaction capture (screenshots) | `evidence/ui-*.md` |
| **4 — synthesis** | merge all findings → coverage matrix filled, UX scores, SOTA gaps | `assessments/summary.md` |
| **5 — backlog** | prioritized new-features + upgrades | `IMPROVEMENTS.md` |
| **6 — harness (optional)** | scaffold Playwright + Vitest + CI to make this repeatable | PR-ready tests |

## 6. Safety constraints (hard rules for all agents)

1. Operate only on throwaway users and mock repos; never touch real projects' `main`.
2. Never run destructive git on real workspaces; rollback/reset only inside mock repos.
3. Clean up every artifact created (users, projects, runs, Redis keys) and log what was
   left behind as intentional evidence.
4. No outbound publishing (PRs, emails) without explicit confirmation.
5. Treat the production DB with care — all writes via documented, reversible steps.
