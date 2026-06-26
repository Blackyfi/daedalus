# Daedalus — Consolidated Quality Assessment

Synthesis of: backend test run, frontend build, live unauthenticated probes
([`../evidence/baseline.md`](../evidence/baseline.md)), a full authed live E2E on a mock
repo ([`../evidence/e2e-live.md`](../evidence/e2e-live.md)), and three SOTA studies
([orchestration](./sota-orchestration.md), [ux-quality](./sota-ux-quality.md),
[security-ops](./sota-security-ops.md)). All verdicts are evidence-backed.

## 1. Ground truth (what's actually true today)

- **Backend: 128/129 pytest pass.** The single failure
  (`test_internal_key_falls_back_to_session_secret`) is an env artifact (the real `.env`
  sets `DAEDALUS_INTERNAL_KEY`, defeating the fallback assertion), not a product bug.
- **Frontend builds clean** (`tsc -b && vite build`, 0 TS errors) but has **zero automated
  tests** — no vitest/playwright/jest, no `*.test/*.spec`. Biggest test-debt gap.
- **Lint: 166 ruff errors** (140 auto-fixable), almost all in test files.
- **Live stack healthy** and **current with HEAD** (images rebuilt 2026-05-27, working tree
  clean) — the old "stale images" caveat no longer applies.
- `/metrics` is **not exposed through Caddy** (serves the SPA) — Prometheus scraping must be
  in-cluster on `backnet`. Confirm this is intended.

## 2. Live E2E coverage (verified PASS/FAIL/PARTIAL)

| Feature | Result | Evidence |
|---|---|---|
| 3FA login (password→OTP→TOTP) | ✅ PASS | 202/202/200, `authenticated:true` |
| Discovery + register | ✅ PASS | mock repo found → project created (201) |
| Project list/stats/git-status/PATCH | ✅ PASS | `max_fix_loops` 3→5 persisted |
| Connectors list | ✅ PASS | 9 connectors |
| Run lifecycle (`shell-demo`, deterministic) | ⚠️ PARTIAL | lease/worktree/transcript/diff OK, but run ended `failed` via idle timeout — **Bug #1** |
| Real-agent fix-loop + snapshot + Argus (yolo) | ✅ PASS | snapshot tag, `a-b`→`a+b`, auto-commit, Argus **pass**, task `done`, ~$0.137, ~53 s |
| Plans (ideas→plan→confirm) | ✅ PASS | LLM proposed 2 tasks → confirm created 2 |
| Merge preview | ✅ PASS | categorization returned |
| KPI timeseries | ✅ PASS | 30 points (asyncpg cast bug stays fixed) |
| system/subscription, system/runners | ✅ PASS | Max 5x, 5h=90%; runners 0/4 |
| Audit log | ✅ PASS | all actions present |
| SPA shell `GET /` | ✅ PASS | 200, `<div id="root">` + bundles |
| IP throttle / lockout | ✅ PASS | 25×401 → 429, Redis key set |

**Conclusion:** the core promise — idea → plan → task → queued run → isolated worktree →
agent edit → auto-commit → LLM verification → done — **works end-to-end on real
infrastructure**, including the expensive real-agent path. Three defects sit at the edges.

## 3. Bugs found (grounded in code + live runs)

| # | Severity | Bug | Location |
|---|----------|-----|----------|
| 1 | High | stdin-prompt connectors never receive EOF; `bash`/`cat`-style agents block until the idle timeout and the run is marked `failed`. `done_signal:exit_code` can never fire. | `talos/pty.py` `write_text` (no stdin close); failure path `talos/runner.py:586-587` |
| 2 | High | Successful Argus (and possibly task) runs can be persisted as `failed`/`exit -1` due to a PTY-teardown race (`ptyprocess.isalive()` → "No child processes") — verdict + task transition are correct, only the run row lies. | `talos/runner.py` `_wait_for_completion` / `runner.py:591` |
| 3 | Medium | `GET /runs/{rid}/argus` is keyed by the **argus** run id, not the task run id, so the intuitive call 404s; clients must first resolve the separate `kind=argus` run. | `api/routes/runs.py:380` |

These are the first three entries in [`../IMPROVEMENTS.md`](../IMPROVEMENTS.md).

## 4. SOTA positioning (2026)

- **Daedalus LEADS:** air-gapped self-hosted orchestration of *local subscription* agents;
  hard cost control (per-run + monthly + per-project caps + Pythia oracle); multi-attach
  live PTY mirroring with input handoff; Argus's no-progress halting + phantom-commit guard.
- **PARITY:** plan decomposition + human review; snapshot/egress safety affordances.
- **LAGS:** (1) **concurrency** — single-runner-per-project vs the field's universal
  parallel-worktree / best-of-N fan-out; (2) **VCS integration** — local-git only, no
  GitHub/GitLab PRs, CI gating, or review comments; (3) **process isolation** — shared-host
  cgroup+iptables, below the gVisor/microVM bar the agent CLIs themselves now assume.

## 5. UX headline

Strong bones (attention inbox, terminal, plan review, diff viewer). Highest-leverage gaps:
reversibility-by-default (one-click undo on merges/ship), evidence-anchored clickable Argus
verdicts, editable plan-review with inline steering comments, and a guided self-hosted
first-run wizard (which also fixes the documented `DAEDALUS_PUBLIC_URL`/WebAuthn-origin
footgun).

## 6. Security headline

Above the self-hosted average but built on a "stack more controls" philosophy. Two patterns
are affirmatively risky, not just missing: **long-lived `~/.ssh` + `~/.claude` creds mounted
into agent runners** (injection → durable identity exfiltration), and **unsigned connector
hot-reload** (write-to-disk RCE). Modern bar = isolation boundaries + brokered short-lived
secrets + signed provenance + tested recovery.

See [`../IMPROVEMENTS.md`](../IMPROVEMENTS.md) for the prioritized, effort-tagged backlog.
