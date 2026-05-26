# Daedalus — Full State, Test & Audit Report

**Prepared:** 2026-05-26
**Auditor:** Claude Opus 4.7 (Claude Code)
**Scope:** Whole repository — backend (FastAPI + Hermes/Talos/Argus/Iris), frontend (React/TS SPA), connectors, deploy stack — plus the **live running deployment** and its **real database**.
**Method:** Static architecture mapping, backend test suite execution (both the deployed image and the working tree), frontend type/build check, live-database inspection, app boot/import verification, connector-pack validation, and targeted security scanning.

---

## 0. Remediation status (updated 2026-05-26, after fixes)

The findings below describe the state **at audit time**. They have since been
remediated on `main`:

| Finding | Status | Where |
|---|---|---|
| F1 Alembic chain corrupt | ✅ Fixed — single linear head, `upgrade head` from empty verified | commit `affe245` |
| F2 Half-finished refactor (45 failing tests, dead notifications) | ✅ Fixed — removal completed, **94 tests pass** | `affe245` |
| F3 notification-prefs path/route | ✅ Resolved by removal | `affe245` |
| F4 `recharts` missing / frontend build | ✅ Fixed — deploy build green; recharts via `npm install` | `affe245` |
| F6 Duplicate Vite config | ✅ Removed + gitignored | `affe245` |
| (bonus) Unbounded fix-loop chains | ✅ Fixed — chain-depth cap + tests | `affe245` |
| F5 mTLS header-trust | ✅ Documented (code + README); in-app verification still optional | commit `a120c74` |
| CI gate (recommended) | ✅ Added (`.github/workflows/ci.yml`) | `a120c74` |
| F7 Broad `except` sweep | ⏳ **Deliberately deferred** — needs a targeted pass on completion paths, not a blind bulk edit on a live system | — |

The stack was rebuilt and redeployed; the API boots, runs `alembic upgrade head`
cleanly, and responds healthy through Caddy.

---

## 1. Executive summary

Daedalus is a **substantial, genuinely-used, well-engineered system**: a self-hosted orchestrator for local AI coding agents with project-scoped task graphs, single-runner-per-project leasing, live PTY mirroring, LLM-based verification (Argus), idea→plan→task review, batch merging, and 3-factor + mTLS auth. The deployed instance has handled **518 runs across 8 projects** and was used to develop itself.

However, the audit surfaced a clear and important split between **what is deployed** and **what is in the working tree**:

| | Deployed build (running ~2 weeks) | Working tree (uncommitted) |
|---|---|---|
| API boots | ✅ Yes | ✅ Yes (82 routes) |
| Backend tests | ✅ 89 passed | ❌ **45 failed, 1 collection error**, 89 passed |
| Frontend builds | ✅ (shipped) | ❌ **`tsc` fails — won't build** |
| Notifications subsystem | ✅ present | ❌ **dead (import error)** |
| Fresh `make up` deploy | ⚠️ chain was valid earlier | ❌ **blocked — alembic has 4 heads** |

**Verdict:** The deployed instance is **functional and stable**. The **current checkout is mid-refactor and not in a releasable state**, and — most importantly — **a clean, from-scratch deployment is currently impossible** because the Alembic migration chain is corrupt (multiple heads + a duplicate revision). These are fixable and mostly mechanical.

**Top priorities:** (P0) repair the Alembic chain; (P0) finish or revert the in-flight refactor so the tree builds and tests pass; (P1) fix the frontend/backend notification-prefs path mismatch and missing `recharts` install.

---

## 2. What was actually exercised

- ✅ Ran `pytest` inside the **deployed image**: `89 passed in 4.63s`.
- ✅ Ran `pytest` against the **working tree** (full deps, in the prebuilt image): `45 failed, 89 passed` + 1 collection abort.
- ✅ Boot-imported the working-tree app: `import daedalus.main` → **OK, 82 routes**.
- ✅ Import-probed each major subsystem module (found 5 broken modules — see §6).
- ✅ Ran `tsc -b` on the frontend: **fails** with missing exports + missing `recharts`.
- ✅ Queried the **live Postgres** for real projects/tasks/runs/audit data (§4).
- ✅ Ran `alembic heads` / `alembic history`: **chain is broken** (§6, F1).
- ✅ Validated the **real 11-connector pack** against `CONNECTOR_SCHEMA`: **11/11 valid**.
- ✅ Hit the live API (`/api/v1/auth/status` → 200, `/metrics` → 200, root → 200).
- ✅ Security scan: subprocess/SQL/eval usage, broad excepts, secrets.

> **Note on auth:** end-to-end *UI* exercise (clicking through features) requires the 3-factor login (password → email OTP to the owner's real Gmail → TOTP) **and** a browser client cert issued by the internal CA. I cannot complete those factors, so feature behavior was verified at the API/DB/function level and via the real run history rather than by driving the browser.

---

## 3. Architecture (as built)

**Backend** — FastAPI app split into named subsystems, each a separate compose service/process:

- **API** (`daedalus/api/routes/*`, 16 routers registered under `/api/v1`): auth, webauthn, projects, merges, tasks, ideas, notes, connectors, plans, runs, audit, discovery, system, diagnostics, kpis, internal.
- **Cerberus auth** (`daedalus/auth/*`): 3-factor (Argon2id password → single-use email OTP/magic-link → TOTP/recovery codes), signed + **cert-bound** sessions with idle + hard expiry, WebAuthn, mTLS client-cert minting, full audit log.
- **Hermes scheduler** (`daedalus/hermes/*`): Redis-backed per-project lease (atomic Lua claim), priority lanes (urgent/default/bg), dependency blocking, orphan reclaim, rate-limit pause handling.
- **Talos PTY runner** (`daedalus/talos/*`): multi-run PTY supervisor, cgroup isolation, transcript capture, auto-commit, pre-yolo snapshots.
- **Argus verifier** (`daedalus/argus/*`) + **planning** (`daedalus/planning/*`): LLM verdicts (pass/partial/fail) and idea→task plan proposals, with deterministic fallback when `LLM_BASE_URL` is down.
- **Merge** (`daedalus/merge/*`): batch merge preview/execute/resolve/ship with claim-check + reconcile.
- **Iris** (`daedalus/iris/*`): WebSocket PTY fan-out with multi-attach input-holder arbitration.
- **Support:** notifications, storage (MinIO/S3), observability (Prometheus/OTel), discovery, pythia (subscription oracle), connectors (+usage parser).

**Frontend** — React 18 + TS + Vite + Tailwind + xterm.js SPA. 9 pages (ProjectList, Project, KPI, Connectors, Audit, Security, Algorithms, Login, Account) and ~22 components. TanStack Query polling (tab-visibility aware), Zustand state, xterm PTY mirroring over `ws://…/ws/pty/{run_id}`.

**Deploy** — `docker compose`: caddy (mTLS termination), frontend, api, iris, hermes, talos, argus-worker, postgres (+pg-backup sidecar), redis, minio, litellm, agentnet-firewall, optional vLLM (`--profile llm`).

The design is documented thoroughly in `project-plan.md` and `TODO.md`, and the code largely matches the documented spec.

---

## 4. Live system state & real usage (from the production database)

| Entity | Count |
|---|---|
| Projects | 8 (all active: agentic-ai-pipeline, cloud-auto-sync-save, daedalus, lycee_hotelier_ui_scrapper, manga_manhwa_upscaling_library, metis-ai-picture, partage-emailbox-forwarder, todo_app) |
| Tasks | 143 |
| Runs | 518 |
| Audit events | 394 |
| Connectors | 11 |
| Plan proposals | 8 |
| Users | 1 (owner) |
| Sessions | 18 |

**Run outcomes (518):** `completed` 439 (**84.7 %**), `aborted_unsafe` 41 (7.9 %), `failed` 31 (6.0 %), `cancelled` 7 (1.4 %).
**Run kinds:** task 288, argus 221, planning 9.
**Task status (143):** done 134 (**93.7 %**), backlog 4, needs_fixes 4, ready 1.
**Argus verdicts (224):** pass 129 (57.6 %), partial 43 (19.2 %), fail 52 (23.2 %).

**Interpretation:** This is real, sustained usage with a healthy completion rate. The 7.9 % `aborted_unsafe` rate is notable — the safety guard (phantom-commit / unsafe-state detection) fires fairly often and is worth a closer operational look, but it is failing *safe*. Argus rejects ~42 % of runs (partial+fail), i.e. the verification loop is actively doing work rather than rubber-stamping.

---

## 5. Test & build results

### Backend
- **Deployed image:** `89 passed`. ✅
- **Working tree:** `45 failed, 89 passed` + `test_notifications.py` aborts collection. ❌
  - Failures cluster in `test_notification_prefs_api.py` (12), `test_project_ideas.py` (12), and `test_notifications.py` (collection error) — all caused by the removed `UserNotificationPref` model and unregistered route modules.

### Frontend
- `npx tsc -b` **fails** (`npm run build` = `tsc -b && vite build`, so the build does not produce a `dist/`). Errors:
  - `api.ts` missing exports: `AutoRunStatus`, `AutoRunConfigPatch`, `NotificationPrefs`, `NotificationPrefsPatch`, `ProjectIdea`, `ProjectIdeaPromoteIn`, `ProjectIdeaStatus`, `updateProjectIdea`.
  - `Cannot find module 'recharts'` (declared in `package.json` but **not installed** in `node_modules`).
  - Several implicit-`any` parameter errors in `AutoRunPanel.tsx` / `ProjectIdeaBox.tsx`.

### Connector pack
- **11/11** connector specs validate against `CONNECTOR_SCHEMA`. ✅

---

## 6. Findings (severity-ranked)

### F1 — 🔴 P0 — Alembic migration chain is corrupt; fresh deploys are blocked
`alembic heads` reports **four heads** (`20260506_0006`, `20260507_0006`, `20260512_0007`, `20260512_0009`) and warns *"Revision 20260506_0006 is present more than once."* `alembic history` fails outright: *"Requested revision 20260512_0007 overlaps with other requested revisions."*
`backend/entrypoint.sh` runs `alembic upgrade head` on boot. With multiple heads that command errors, so **a new environment following the README quickstart (`make up`) cannot migrate the database.** The existing deployment only works because it was migrated incrementally *before* the chain was corrupted (live DB is at `20260512_0009`).
**Fix:** de-duplicate revision IDs and re-link `down_revision` into a single linear chain (or add an explicit merge revision), then test `alembic upgrade head` on an empty DB.

### F2 — 🔴 P0 — Working tree is a half-finished refactor; tree does not build/test
A large in-flight change gutted `db/models.py` (net −330 lines: `UserNotificationPref` and project-ideas/autorun-policy surface removed; migration `20260513_0010_autorun_policy_fields.py` and `test_merge_batch_claim_and_reconcile.py` deleted). `main.py` was updated to stop registering the `notification_prefs`, `autorun`, and `project_ideas` routers, **but the modules and tests were left dangling.** These modules **fail to import**:
`daedalus.notifications.dispatcher`, `daedalus.notifications.usage_monitor`, `daedalus.api.routes.notification_prefs`, `daedalus.api.routes.autorun`, `daedalus.api.routes.project_ideas` — all `ImportError: cannot import name 'UserNotificationPref'`.
Consequence: the **notifications subsystem is effectively dead** in the new code (the scheduler calls `notify(...)`; that path will raise at runtime when a run completes), and 45 tests fail. The API still *boots* only because those modules are no longer imported at startup.
**Fix:** decide the direction and finish it — either restore `UserNotificationPref` (+ re-register routers) or fully remove the dependent modules, tests, and frontend pages.

### F3 — 🟠 P1 — Frontend ↔ backend notification-prefs path mismatch
`AccountPage.tsx` calls `/api/v1/account/notification-prefs`, but the backend route is defined as `/api/v1/notification-prefs` (no `account` segment) — and in the working tree it isn't registered at all. Either path returns 404 against both the live build and the working tree. The Account page's notification settings cannot work as written.
**Fix:** align the paths (and re-register the router if the feature is being kept).

### F4 — 🟠 P1 — `recharts` declared but not installed
`KPIPage.tsx` imports `recharts` (in `package.json` as `^2.15.0`) but it is absent from `node_modules`, so the KPI dashboard fails to type-check/build. `npm install` was not re-run after the dependency was added.
**Fix:** `npm install` and commit the lockfile.

### F5 — 🟡 P2 — mTLS reduces to header trust at the app layer
`auth/dependencies.py:24` reads the client-cert fingerprint **from the `X-Client-Cert-Fingerprint` header** (Caddy sets it after verifying the cert), falling back to the literal `"no-mtls"` sentinel. This is the standard reverse-proxy-trust model and is fine **only while the API port is never reachable except through Caddy.** The api container currently exposes `8000/tcp` internally with no host mapping, so this holds — but if anyone publishes the port or runs a second ingress, a client could spoof the header and bind a session to an arbitrary fingerprint. Also, when mTLS is disabled every session shares the `"no-mtls"` fingerprint, so cert-pinning provides no isolation in that mode.
**Fix:** document the hard requirement that the API is never exposed directly; consider a shared-secret header between Caddy and the API, or verifying the cert in-app.

### F6 — 🟡 P2 — Duplicate Vite config files
`vite.config.ts` (tracked) coexists with untracked `vite.config.js` + `vite.config.d.ts` (build leftovers). This is ambiguous and can cause "edited the wrong file" confusion.
**Fix:** delete `vite.config.js`/`vite.config.d.ts`, keep `.ts`, and ensure they're git-ignored.

### F7 — 🟢 P3 — Minor quality notes
- **83** broad `except Exception`/bare-`except` sites in backend — some intentional best-effort, but worth auditing for silent failure-swallowing (e.g. in completion/notification paths).
- The api process `/metrics` exposes only default Python/process collectors; the meaningful app counters (`RUNS_COMPLETED_TOTAL`, `QUEUE_DEPTH`, Argus verdicts) live in the Hermes/Talos processes — fine, but confirm Prometheus scrapes those targets.
- `frontend/tsconfig*.tsbuildinfo` write errors during `tsc` (permission) — incremental build artifacts owned by another uid; cosmetic.
- A stray empty `test_write_root` file sits in the repo root.

---

## 7. Security assessment

**Strong points (verified):**
- **No command-injection surface:** every subprocess call uses `asyncio.create_subprocess_exec` with argument lists. **No `shell=True`, no `os.system`, no `eval`/`exec`.**
- **No SQL injection:** all DB access is via SQLAlchemy ORM / parameterized queries; no f-string/`%`-formatted SQL found.
- **Auth is serious:** Argon2id passwords; single-use, hashed, 15-min email OTPs (Blake2b with pepper); RFC-6238 TOTP + hashed recovery codes; HMAC-signed, cert-bound sessions with idle + hard expiry; WebAuthn; comprehensive audit logging (394 events live).
- **Egress control + secrets:** agentnet egress firewall sidecar, pg-backup sidecar, secrets via `deploy/secrets/`, the `[dev]` extras excluded from prod images.
- **0 TODO/FIXME/HACK** markers in backend source.

**Watch items:** F5 (header-trust mTLS), the broad-except swallowing (F7), and ensure the in-flight refactor doesn't leave a half-wired notifications path that throws inside run-completion (F2).

---

## 8. What works well

- Clean subsystem decomposition that maps 1:1 to compose services and to the documented spec.
- Thoughtful concurrency model (atomic Lua lease, lanes, dependency blocking, orphan reclaim).
- Deterministic LLM fallbacks so dev environments work without a model server.
- Genuinely battle-tested: 518 real runs, 84.7 % completion, active Argus rejection — this is not a toy.
- Strong injection-resistant coding patterns throughout.
- Excellent operator docs (`README.md`, `project-plan.md`, `TODO.md`, `deploy/agentnet.md`).

---

## 9. Recommended remediation order

1. **F1 — repair the Alembic chain** (unblocks all fresh deploys). *Highest priority.*
2. **F2 — land or revert the refactor**: restore `UserNotificationPref` or delete the dependent modules/tests/frontend; get `pytest` and `tsc -b` green.
3. **F4** `npm install` (recharts) + commit lockfile; **F3** fix the notification-prefs path.
4. **F6** remove duplicate Vite configs; clean the stray `test_write_root`.
5. **F5** document/enforce the "API never exposed directly" invariant; consider in-app cert verification.
6. **F7** sweep broad excepts on the completion/notification paths; confirm Prometheus scrape targets.

A useful gate going forward: a CI job that runs `alembic upgrade head` on an empty DB, `pytest`, and `npm run build` would have caught F1–F4 before commit.

---

## 10. Evidence appendix (commands run)

- `docker exec deploy-api-1 python -m pytest -q` → `89 passed`.
- Working tree in prebuilt image: `pytest --ignore=tests/test_notifications.py` → `45 failed, 89 passed`; `import daedalus.main` → `82 routes`; per-module import probe → 5 modules `FAIL`.
- `alembic heads` → 4 heads + duplicate-revision warning; `alembic history` → overlap error.
- `npx tsc -b` → missing exports + `recharts` not found.
- Postgres: counts and distributions in §4; `alembic_version = 20260512_0009`.
- 11/11 connector specs valid against `CONNECTOR_SCHEMA`.
- `grep` for `shell=True|os.system|eval|exec|format-SQL` → none in app code.
