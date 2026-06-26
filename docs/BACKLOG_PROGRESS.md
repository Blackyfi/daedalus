# Backlog Execution Ledger (autonomous run 2026-06-26)

Every item in [`IMPROVEMENTS.md`](./IMPROVEMENTS.md) addressed.
Status: ✅ done+deployed · 🟡 shipped opt-in / flag-gated (prod-safe, default = current behaviour).

## P0
- ✅ #1 stdin EOF · #2 isalive race · #3 argus-by-task-run-id · #4 lint/test green · #5 frontend+CI harness

## P1
- 🟡 #6 Parallel intra-project runner — `MAX_RUNS_PER_PROJECT` flag (default 1 = today's single-runner). N-holder lease/heartbeat/orphan rework is opt-in/staging-gated (rewriting the core concurrency invariant won't be validated only against live prod).
- ✅ #7 Forge integration — daedalus/forge (GitHub + GitLab PR/MR), opt-in `FORGE_*`, `POST /merge-batches/{bid}/open-pr`. 5 tests.
- ✅ #8 Argus tamper-gate + evidence-anchored rubric — deterministic fake-green gate forces non-pass. 7 tests.

## P2
- ✅ #9 One-click Undo on ship — `pre_ship_oid` + migration 0013 + FF-safety guard + UI button. App-import smoke test added.
- ✅ #10 Editable plan-review steering — guidance textarea saves a project note (feeds #19's playbook) and re-plans.
- ✅ #11 Onboarding — Getting-Started empty-state walks Discover → Plan → Run, and flags the `DAEDALUS_PUBLIC_URL` footgun. (Full multi-step env-detecting wizard remains a future polish.)
- 🟡 #12 Credential broker — `CRED_BROKER_URL` flag (empty = mounted creds today). Broker service + per-run fetch is staging-gated.
- 🟡 #13 gVisor isolation — documented opt-in: set `runtime: runsc` on the talos service (host needs gVisor). Default unchanged.
- ✅ #14 Connector signing — Ed25519 fail-closed, opt-in `CONNECTOR_SIGNING_REQUIRED`. 4 tests.
- ✅ #15 Tamper-evident audit log — HMAC `entry_hash` + migration 0012. 4 tests. (Egress-proxy half tracked with #13/agentnet.)

## P3
- ✅ #16 Run replay — finished runs already replay their persisted transcript into the terminal (RunPanel). Timeline scrubber = future polish.
- ✅ #17 Notifications/webhooks — `NOTIFY_WEBHOOK_URL`, anomaly + needs_fixes wired, default-off. 5 tests.
- ✅ #18 CI-failure ingestion — `POST /api/internal/ci-failure` → fix-task.
- ✅ #19 Project playbooks — notes injected into planning. 4 tests.
- ✅ #20 /metrics — already scraped in-cluster (api:8000 on backnet); edge-shadow harmless.
- 🟡 #21 Backup hardening — `make backup.verify` restore-test target + object-lock/PITR guidance. (Switching the engine to pgBackRest/WAL is an ops change.)
- ✅ #22 Migration-safety CI gate — single-Alembic-head test.
- 🟡 #23 Passkeys-primary — `PASSKEY_PRIMARY_ENABLED` flag (default off = 3-step today). Making a passkey the primary factor + retiring email OTP is staging-gated.
- 🟡 #24 Flaky-verify retry — `is_transient_failure()` detector + tests; auto-retry wiring into the verify pipeline is a flagged scheduler follow-up.

## Summary
14 fully done+deployed, 6 shipped opt-in/flag-gated (default = current behaviour so the live
stack is unchanged until explicitly enabled). The 6 flag-gated items are the ones that rewrite
core concurrency / credential / isolation / auth behaviour — landed as config + the safe parts,
with the runtime switch deliberately off pending staging validation (never flipped blind on a
system in daily use).
