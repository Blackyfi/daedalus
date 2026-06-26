# Daedalus E2E live evidence log

- **Date:** 2026-06-26
- **Driver:** automated QA pass against the LIVE `deploy/` compose stack via Caddy `https://localhost:9443` (`REQUIRE_CLIENT_CERT=false`).
- **Run suffix:** `20260626-204254` (all throwaway artefacts share this suffix).
- **Throwaway owner:** `qa-20260626-204254@daedalus-qa.dev` (user id `e8abfc9d-2611-4531-a94c-f61a210d5720`).
- **Mock repo:** host `/home/ad-veritas/git-repos-projects/daedalus-qa-20260626-204254` → container `/workspaces/daedalus-qa-20260626-204254` (verified via `docker inspect deploy-talos-1`: host `git-repos-projects` is bind-mounted to `/workspaces`, rw).
- **Project id:** `3cb58419-cc0f-48c8-97b1-37114ca7aad2`.

All artefacts were cleaned up at the end (see Cleanup section).

---

## Setup

### Mock repo
Fresh git repo seeded with an obvious one-line bug and a pytest that asserts the correct behaviour:

- `src/calc/math_ops.py` — `add(a, b)` deliberately `return a - b` (bug); `mul(a, b)` correct.
- `tests/test_math_ops.py` — `test_add` asserts `add(2,3) == 5`; `test_mul` asserts `mul(2,3) == 6`.
- `README.md`, `pytest.ini` (`pythonpath = .`).
- Local git identity `QA Bot <qa-bot@daedalus-qa.dev>`. Initial commit `5cda0df` on branch `main` (renamed from default `master` to match the registered default branch). Owned by host uid 1000 = container `daedalus` uid 1000, so Talos can read/write.

### Auth (3FA)
- `python -m daedalus.cli init --email qa-…@daedalus-qa.dev --password 'Str0ng-QA-pass!234' --role owner` → created user + emitted TOTP secret `WBDCU42GVZZ54T7JUEOCKLKOVIPNLD4X` + 10 recovery codes.
- `POST /api/v1/auth/password` → **202** `{"status":"otp_sent"}`.
- Overwrote the latest `EmailOTP.code_hash` to `email_otp._hmac('12345678')` via `docker exec deploy-api-1 python …` (used `get_sessionmaker()`, `get_engine()`), committed. Patched OTP id `469c81e0-…`.
- `POST /api/v1/auth/email-otp {code:'12345678'}` → **202** `{"status":"totp_required"}`.
- `POST /api/v1/auth/totp {code:165582}` (`pyotp.TOTP(secret).now()`) → **200** `{"status":"ok","user":{…role:owner}}`, session cookie set.
- `GET /api/v1/auth/status` → **200** `{"authenticated":true,"user":{…}}`.

---

## Feature evidence

### 3. Discovery — PASS
- `GET /api/v1/discover/repos` → **200**, 14 repos. Mock repo present: `{"name":"daedalus-qa-20260626-204254","path":"/workspaces/daedalus-qa-20260626-204254","already_registered":false}`.
- `POST /api/v1/discover/register {repos:[{path:/workspaces/daedalus-qa-20260626-204254,name:daedalus-qa-204254,git_default_branch:main,default_connector_id:shell-demo}]}` → **201**, created project `3cb58419-…`.

### 4. Project — PASS
- `GET /api/v1/projects` → **200**, 9 projects, ours present.
- `GET /api/v1/projects/stats` → **200**, per-project status histograms (ours all-zero initially).
- `GET /api/v1/projects/{pid}/git-status` → **200** `{"is_git_repo":true,"branch":"main","behind_count":0,"ahead_count":0,"needs_pull":false}`.
- `PATCH /api/v1/projects/{pid} {max_fix_loops:5}` → **200**, value persisted (`max_fix_loops=5`).

### 5. Connectors — PASS
- `GET /api/v1/connectors` → **200**, 9 connectors, all enabled. Slug field is `connector_id`: `claude-code`, `claude-code-interactive`, `claude-multi-confirm`, `claude-multi-yolo`, `codex-confirm`, `codex-yolo`, `qwen-coder-confirm`, `qwen-coder-yolo`, `shell-demo`. **Note:** no `claude-code-yolo` slug exists — the yolo Claude connector is `claude-code` (`permission_profile: yolo`).

### 6. Task + run lifecycle (deterministic, shell-demo) — PARTIAL (lifecycle works; connector cannot reach success — see Bug #1)
- `POST /api/v1/projects/{pid}/tasks` (connector `shell-demo`, profile yolo) → **201**, task `a928e86b-…`.
- `POST /api/v1/tasks/{tid}/run` → **202**, run `680c8f4c-…` state `queued`.
- **Hermes claim:** Redis key `hermes:project_lease:3cb58419-…` present throughout the run.
- **Per-run worktree:** Talos log `talos.executing workdir=/workspaces/daedalus-qa-20260626-204254/runs/680c8f4c-…`; on disk `runs/680c8f4c-…/` (owner root). `pty.spawned pid=16 command=bash`.
- **Transcript:** `GET /runs/{rid}/transcript/text` → **200**, echoes the streamed prompt (`shell-demo deterministic run\n\nwrite the prompt to the task file`).
- **Diff:** `GET /runs/{rid}/diff` → **200**, empty (shell-demo writes to `/tmp`, not the worktree).
- **Terminal state:** state `failed`, `exit_code=null`, started `18:45:50` → finished `18:47:51`. Talos log `talos.idle_output_exceeded idle_seconds=120` then `talos.completed`. token/cost fields all null.
- Task `a928e86b` moved to `needs_fixes` (failed run).
- **Root cause (Bug #1):** `shell-demo` runs `bash -lc "cat >/tmp/daedalus-task.txt && printf DONE"`. Talos streams the prompt via `PtySession.write_text()` (`talos/pty.py`) which never sends EOF / closes stdin, so `cat` blocks forever and the run only dies on the 2-min idle timeout. Any `stdin_prompt` connector that reads until EOF cannot complete via the live run path.

### 7. Argus + fix-loop + snapshot (REAL agent, `claude-code` yolo) — PASS
Quota at run time: Max 5x, 5-hour window 90% (critical) — single tiny run attempted and it was cheap.
- Task `e32fe0e6-…` "Fix the add() bug so the test passes", connector `claude-code`, profile yolo. `POST /tasks/{tid}/run` → **202**, run `4b696735-…`.
- **Pre-yolo snapshot:** git tag `daedalus-snap/4b696735-…` created before the agent ran. `GET /runs/{rid}/snapshot` → **200** `{"git_tag":"daedalus-snap/4b696735-…","note":"pre-yolo snapshot"}`.
- **Agent edit + auto-commit:** `GET /runs/{rid}/diff` shows `add()` changed from `return a - b` to `return a + b` (+ an auto-appended `.gitignore`). Worktree git log: `2ec9f80 Fix add() to return a+b…` (agent commit) then `98f552b daedalus: Fix the add() bug…` (Daedalus auto-commit). Worktree file = `return a + b`.
- **Run result:** state `completed`, `exit_code=0`, `token_input=102`, `token_output=9092`, `cost_usd_micros=136686` (~$0.137), wall `18:51:50`→`18:52:43` (~53 s).
- **Argus verdict:** report row (id `60e44a8c-…`) `verdict=pass`, summary "Bug fixed: add() now returns a+b instead of a-b. Diff confirms the change… Verify commands exited 0. Acceptance criteria met." `GET /runs/{argus_run_id}/argus` → **200** (argus run id `d88a49d0-…`). Task `e32fe0e6` moved to `done`, `fix_loop_count=0`.
- **Bug #2 (cosmetic):** the Argus *run row* `d88a49d0-…` is marked `state=failed exit_code=-1`, even though its verdict (`pass`) was captured and the task correctly went to `done`. Cause: a PTY teardown race in `talos/runner.py` `_wait_for_completion` → `pty.is_running` → `ptyprocess.isalive()` raised `PtyProcessError: isalive() encountered condition where "terminated" is 0, but there was no child process` (the fast `printf` argus stub exits before the poll loop checks). The verdict is unaffected, but the run row state is misleading.
- **Bug #3 (minor/UX):** `GET /runs/{task_run_id}/argus` returns **404** — the report is keyed by the *argus* run id, not the task run id. The SPA/consumer must know to query the argus run; querying the task run (the intuitive id) 404s.

### 8. Plans — PASS
- `POST /projects/{pid}/ideas` ×2 → **201** each (subtract() helper; type hints). `GET …/ideas` → **200**, count 2.
- `POST /projects/{pid}/plan` → **202** `{"run_id":"73ac6da4-…","status":"queued"}`. Planning run `73ac6da4` completed (exit 0) in ~30 s.
- `GET /projects/{pid}/plans?status=pending` → **200**, 1 pending plan `11294653-…` with 2 LLM-generated `proposed_tasks` derived from the ideas.
- `POST /plans/{plan_id}/confirm {archive_source_ideas:true}` → **200**, plan `confirmed`; created 2 tasks (`Add subtract() helper…`, `Add type hints…`). (First confirm without a body → **422**, as `PlanConfirm` is required.)

### 9. Merge — PASS
- `POST /projects/{pid}/merge-batch/preview {}` → **200**, categorization `{"default_branch":"main","proposed_integration_branch":"daedalus-merge-…","plans":[]}` (empty because no task has a merge-eligible branch yet).

### 10. Read-only page APIs (SPA backers) — PASS
- `GET /kpis/projects/{pid}/task-status-timeseries?days=30` → **200**, 30 daily points (KPI asyncpg cast bug confirmed FIXED). 2026-06-26 point shows `needs_fixes:1`.
- `GET /system/subscription` → **200**, `{"plan":"Max 5x","plan_tier":"max_5x","weekly_used_pct":38.0,"five_hour_used_pct":90.0}` (live OAuth profile for the underlying Claude account).
- `GET /system/runners` → **200** `{"max_concurrent_projects":4,"active_count":0}`.
- `GET /audit?limit=25` → **200**, our actions present: `auth.password_ok`, `auth.otp_ok`, `auth.login`, `project.create_via_discovery`, `project.update`×2, `task.create`, `task.enqueue`, `plan.enqueue`, `plan.confirm`.
- `GET https://localhost:9443/` → **200**, SPA shell `<!doctype html>… <title>Daedalus</title>`, `<div id="root">`, bundled `/assets/index-vg7sG_T5.js`.

### 11. Failure modes — PASS
- **IP throttle / 429:** 26× `POST /auth/password` with a non-existent email (`nobody-20260626-204254@…`, wrong passwords) from the proxy IP `172.25.0.1`: attempts 1–25 → **401** `invalid credentials`; attempt 26 → **429** `too many attempts`. Redis `auth:ip_fail:172.25.0.1 = 25` (ttl 3600). (`ip_ban_threshold=25`.) Per-account lockout was NOT exercised because a non-existent email has no `User` row to lock; only the IP counter increments. Key deleted afterward; existing cookie session remained valid (`/auth/status` → authenticated:true).
- Git-behind run was not induced (no remote on the throwaway repo; skipped per instructions).

---

## Bugs / findings summary
1. **`shell-demo` (and any `stdin_prompt` EOF-reading connector) cannot complete via the live run path** — Talos never closes/EOFs PTY stdin after streaming the prompt, so `cat`-style commands hang until the idle timeout → run `failed`. `talos/pty.py:write_text` has no EOF; `talos/runner.py:431`.
2. **Argus run row mis-stated as `failed`/`-1`** on a successful verification — PTY teardown race in `talos/runner.py` `_wait_for_completion` (`ptyprocess.isalive()` `No child processes`). Verdict (`pass`) and task transition (`done`) are correct; only the run-row state is wrong.
3. **`GET /runs/{rid}/argus` is keyed by the argus run id, not the task run id** — querying the intuitive task run id 404s; the consumer must resolve the separate `kind=argus` run first.

## What worked end-to-end
3FA login, discovery+register, project CRUD/stats/git-status, connectors list, run lifecycle plumbing (Hermes lease, per-run worktree, transcript, diff, terminal state, idle-timeout enforcement), **the full real-agent fix loop** (pre-yolo snapshot tag → agent edit → agent commit → Daedalus auto-commit → Argus `pass` → task `done`, ~$0.137, ~53 s), planning (ideas→LLM plan→confirm→tasks), merge preview, all read-only page APIs (KPIs/subscription/runners/audit), SPA shell, and IP-throttle 429.
