# Daedalus — Prioritized Improvement Backlog

Derived from evidence-backed testing + SOTA research (see [`assessments/`](./assessments/)
and [`evidence/`](./evidence/)). Each item: **problem → change → why better → effort
(S≤1d / M≤1wk / L>1wk) → risk → files**. Ordered by impact ÷ effort.

---

## P0 — Correctness bugs (found live, confirmed in code)

### 1. stdin-prompt connectors hang until idle-timeout → `failed` `[S]`
- **Problem:** `shell-demo` (and any `stdin_prompt` connector whose command reads stdin to
  EOF, e.g. `bash`/`cat`) never receives EOF; Talos writes the prompt but never closes PTY
  stdin, so the process blocks until the 2-min idle timeout and the run is marked `failed`.
  `done_signal:{kind:exit_code}` can therefore never fire. **Verified live.**
- **Change:** after writing the stdin prompt for non-interactive connectors, send EOF
  (Ctrl-D / close master once) — gate on a connector flag like `input_format.close_stdin`
  (default true for `stdin_prompt`) so interactive agents are unaffected.
- **Why better:** the bundled smoke connector currently can't pass; this is the canary for
  the whole stdin path.
- **Risk:** low (flag-gated). **Files:** `talos/pty.py` (`write_text`/`close`),
  `talos/runner.py` (prompt write), `connectors/schema.py`, `connectors/shell-demo.json`.

### 2. PTY-teardown race mislabels successful runs as `failed`/`exit -1` `[S]`
- **Problem:** `ptyprocess.isalive()` raising "No child processes" during teardown can yield
  `exit_code=-1` → `state="failed"` even when the agent succeeded and Argus returned `pass`
  / the task went `done`. The run row lies. **Observed on the Argus run during live E2E.**
- **Change:** treat `ChildProcessError`/`No child processes` in `_wait_for_completion` as a
  benign already-exited signal; fall back to the last known exit status (or `0` when a done
  signal was seen) instead of `-1`.
- **Why better:** the run list/Argus card must not show green outcomes as red.
- **Risk:** low. **Files:** `talos/runner.py:480-591`.

### 3. `GET /runs/{rid}/argus` keyed by argus-run id, not task-run id `[S]`
- **Problem:** `ArgusReport.run_id` stores the `kind=argus` run id; the endpoint queries it
  directly, so calling with the task run id 404s. **Verified live.**
- **Change:** accept the task run id — resolve the linked argus run (or store
  `task_run_id` on `ArgusReport` and query that). Keep back-compat for the argus id.
- **Why better:** the SPA shows the verdict on the task run; the API should answer the
  obvious id. **Risk:** low. **Files:** `api/routes/runs.py:373-384`, `db/models.py`,
  possibly a migration.

### 4. Make the failing test + lint green `[S]`
- Fix `test_internal_key_falls_back_to_session_secret` to isolate env
  (`monkeypatch.delenv("DAEDALUS_INTERNAL_KEY")`); run `ruff check --fix` (140 auto-fixable)
  and review the ~21 unsafe ones. **Files:** `tests/test_security_hardening.py`, repo-wide.

---

## P1 — High-impact: quality harness + the two biggest SOTA gaps

### 5. Frontend + E2E test harness in CI `[M]`
- **Problem:** zero frontend tests; the live E2E is manual. Regressions ship blind.
- **Change:** add **Vitest + Testing Library** for components (TaskBoard, DiffViewer,
  RunPanel envelope handling, PlanReview) and **Playwright** for the 3FA login + core
  flow, reusing the documented OTP-bypass auth recipe against an ephemeral stack. Wire a
  GitHub Actions (or local `make ci`) gate: pytest + ruff + tsc + vitest + playwright.
- **Why better:** turns this whole report into a repeatable gate. **Effort:** M.
- **Files:** `frontend/` (new `vitest.config`, `playwright.config`, `tests/`), `Makefile`,
  `.github/workflows/`.

### 6. Parallel intra-project worktree fan-out + best-of-N `[L]`
- **Problem (#1 SOTA gap):** single-runner-per-project; every peer (Devin, OpenHands,
  Cursor, Conductor, Sculptor) runs parallel worktrees / best-of-N ensembles. Plumbing
  (per-run worktrees, leases, cgroups) already exists.
- **Change:** allow N concurrent runs per project on isolated worktrees behind a
  per-project concurrency setting; optionally run the same task K times and let Argus/an
  ensemble judge pick the winner to merge.
- **Why better:** throughput + quality (best-of-N beats one-shot on SWE tasks).
- **Risk:** med (lease model, queue fairness). **Files:** `hermes/scheduler.py`,
  `hermes/leases.py`, `hermes/client.py`, project settings, SPA RunPanel.

### 7. Forge integration: GitHub/GitLab PRs + CI gating + review comments `[L]`
- **Problem (#2 SOTA gap):** local-git only; no PRs, no CI signal, no review threads.
- **Change:** optional, off-by-default (preserve air-gap) connector to push branches, open
  PRs, ingest CI status into the fix-loop, and render Argus findings as PR review comments.
- **Why better:** fits real team workflows; closes the loop with existing CI.
- **Files:** new `forge/` module, merge/ship, Argus findings renderer, settings.

### 8. Argus trust upgrade: tamper gate + evidence-anchored rubric + cascade `[M]`
- **Problem:** verdicts are a single LLM call; SOTA shows frontier models reward-hack
  (~30% on RE-Bench) — deleting/weakening tests, phantom commits, fake-green.
- **Change:** (a) a **deterministic tamper/"fake-done" gate** (block auto-merge if tests
  were deleted/skipped/weakened, assertions removed, or claimed commit absent — extends the
  existing phantom-commit guard); (b) **locked discrete rubric** (0/1/2 per criterion) where
  every finding quotes exact diff/test lines and "pass" is capped without evidence;
  (c) run Argus as a **cascade** — free deterministic gates (tests/lint/tamper) → cheap
  judge → frontier + multi-judge only on boundary verdicts (RouteLLM-style, ~85% cost cut).
- **Why better:** more trustworthy *and* cheaper *and* clickable-to-proof. **Files:**
  `argus/verifier.py`, `hermes/scheduler.py`, SPA Argus verdict card.

---

## P2 — UX leverage + security hardening

### 9. Reversibility-by-default: one-click Undo on ship/merge/force-push `[M]`
- Snapshot before every destructive op (already done pre-yolo); expose **Undo** on the
  merge-batch and ship flows (tag the pre-ship `main`, offer `reset --hard` back).
  Irreversibility is the top killer of agent-tool trust. **Files:** `merge/ship.py`,
  `merge/executor.py`, MergeBatchModal, runs API.

### 10. Editable plan-review with inline steering comments `[M]`
- Plan review and diff review should let the user leave inline comments that the agent
  consumes ("Return of Control"), not just approve/reject. **Files:** PlanReview, DiffViewer,
  planning, internal planning ingest.

### 11. Guided self-hosted first-run wizard `[M]`
- Web wizard: auto-detect/set `DAEDALUS_PUBLIC_URL` + WebAuthn origin (the documented
  footgun), connect a repo, run one demo task end-to-end. **Files:** new onboarding route,
  CLI `init` handoff, settings.

### 12. Broker short-lived scoped creds instead of mounting `~/.ssh` + `~/.claude` `[L]`
- **Highest security risk:** long-lived SSH key + Claude cred mounted into agent runners +
  shell + web egress = one-step injection→exfiltration; creds outlive the run. Move to a
  per-run credential broker / proxy (sign git ops outside the sandbox, mint short-lived
  tokens). **Files:** `talos/runner.py`, compose mounts, new broker sidecar.

### 13. Container-per-agent isolation (gVisor `runsc`) `[L]`
- Runners are shared-host-kernel containers — below the Tier-1 default the agent CLIs ship.
  Wrap runs in gVisor (after a seccomp + drop-caps + rootless pass). Makes `yolo` actually
  contained. **Files:** `talos/`, compose runtime config, `deploy/`.

### 14. Sign connectors + fail-closed verify on load `[M]`
- Unsigned hot-reload of on-disk packs is a latent RCE (write-to-disk is the exploit).
  `cosign verify-blob` fail-closed, content-address packs, sign in CI. (Spec §10.6 was
  deferred — the live egress firewall + agent FS access change the calculus.) **Files:**
  `connectors/loader.py`, CI, `deploy/`.

### 15. App-layer egress proxy + tamper-evident audit log `[M]`
- Add a domain/SNI-allowlisting egress proxy in front of the iptables backstop (L3/L4 is
  DNS-exfil bypassable); make the audit log insert-only + hash-chained (OWASP A09).
  **Files:** `deploy/agentnet-firewall/`, `auth/audit.py`, `db/models.py`.

---

## P3 — Strategic / smaller wins

- **16. Run replay / timeline view** `[M]` — scrub a finished run's PTY transcript with
  cost/step markers (terminal already persists transcripts).
- **17. Notifications/webhooks + off-platform Work Reports** `[S]` — ping only on blocking
  events (needs-fixes, conflict, rate-limit pause, anomaly); reuse the Gmail/SMTP channel.
- **18. CI-failure ingestion into the fix-loop** `[M]` — feed failing CI logs back as the
  next fix-task (pairs with #7).
- **19. Persistent project playbooks / knowledge** `[M]` — let confirmed plans + Argus
  findings accrue into per-project guidance injected into prompts.
- **20. Expose `/metrics` to an authorized scrape path** `[S]` — currently shadowed by the
  SPA at the edge; confirm in-cluster scrape or add an authed route.
- **21. Backup hardening** `[M]` — PITR (WAL archiving / pgBackRest), MinIO Object Lock,
  one off-host copy, automated restore test + alert. **Files:** `deploy/pg-backup/`.
- **22. Migration safety CI gate** `[S]` — assert a single Alembic head + expand/contract,
  preventing the recurring "multiple heads / missing table" incidents.
- **23. Promote passkeys to primary, retire email OTP** `[M]` — NIST SP 800-63-4 prohibits
  email for out-of-band auth; harden the `X-Client-Cert-Fingerprint` trust (signed token,
  not plaintext header) so identity doesn't collapse if the API is reachable off-Caddy.
- **24. Queue fairness + flaky-verify retry** `[S]` — starvation guard across lanes; retry
  a verify once on infra/transient failure before declaring `needs_fixes`.

---

### Suggested sequencing
1. **This week:** P0 #1–4 (bugs + green CI) + #5 (harness) — stop the bleeding, lock the gate.
2. **Next:** #8 (Argus trust), #9 (undo), #11 (wizard) — trust & UX, mostly self-contained.
3. **Then strategic:** #6 (parallelism) and #12/#13 (isolation) — the defining v2 bets.
4. **Opt-in track:** #7 (forge) for teams that want to leave the air-gap.
