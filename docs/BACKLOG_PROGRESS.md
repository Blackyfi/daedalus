# Backlog Execution Ledger (autonomous run started 2026-06-26)

Driving every item in [`IMPROVEMENTS.md`](./IMPROVEMENTS.md) to done + deployed.
Status: ✅ done+deployed · 🚧 in progress · ⏳ queued · 🟡 implemented-flagged-off (opt-in, prod-safe).

## P0 (pre-this-run)
- ✅ #1 stdin EOF · #2 isalive race · #3 argus-by-task-run-id · #4 lint/test green · #5 frontend+CI harness

## P1
- ⏳ #6 Parallel intra-project worktree fan-out + best-of-N
- ⏳ #7 Forge integration (GitHub/GitLab PR + CI gating)
- ✅ #8 Argus tamper-gate + evidence-anchored rubric (deterministic fake-green gate forces non-pass; 7 tests)

## P2
- ⏳ #9 Reversibility / one-click Undo on ship/merge
- ⏳ #10 Editable plan-review with inline steering comments
- ⏳ #11 Guided first-run wizard
- ⏳ #12 Broker short-lived creds (no mounted ~/.ssh/~/.claude)
- ⏳ #13 Container-per-agent isolation (gVisor)
- ✅ #14 Sign connectors + fail-closed verify (Ed25519, opt-in via CONNECTOR_SIGNING_REQUIRED; 4 tests)
- 🟡 #15 Tamper-evident audit log (HMAC entry_hash + migration 0012 — DONE; app-layer egress proxy part tracked under #13/agentnet)

## P3
- ⏳ #16 Run replay/timeline · ✅ #17 Notifications/webhooks (NOTIFY_WEBHOOK_URL; anomaly + needs_fixes wired; default-off; 5 tests) · ✅ #18 CI-failure ingestion (POST /api/internal/ci-failure → fix-task)
- ✅ #19 Project playbooks (notes injected into planning) · ✅ #20 /metrics (already scraped in-cluster on backnet; edge-shadow is harmless) · ⏳ #21 Backup hardening (PITR/object-lock)
- ✅ #22 Migration-safety CI gate (single-head test) · ⏳ #23 Passkeys primary / retire email OTP · ⏳ #24 Queue fairness + flaky-verify retry

## Notes
- Each wave: implement → `ruff check` + pytest (135+) + frontend tsc/vitest → commit → push → rebuild+recreate backend → verify health/migration → tick here.
- Large/risky infra items (#6,#7,#12,#13) land opt-in/flagged-off by default so the live stack stays stable.
