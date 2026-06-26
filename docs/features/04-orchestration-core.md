# 04 — Orchestration Core (Hermes, Talos, Argus)

Source: `backend/daedalus/hermes/`, `backend/daedalus/talos/`, `backend/daedalus/argus/`.

## Queue & scheduler (Hermes)

- **Priority lanes**: urgent / default / bg, scanned in order (`scheduler.py:88-89`).
- **Single-runner-per-project** via project lease (`scheduler.py:140-146`).
- **Atomic claim** via Redis Lua: cap check + lease check + LREM + SET lease in one op
  (`leases.py:52-70`).
- Project leases (`hermes:project_lease:<pid>`) with wall-clock + grace TTL; 5 s heartbeat
  refresh while in-flight (`scheduler.py:776-794`).
- **Orphan recovery** (two-pass): runs in running/claimed without a live lock →
  `aborted_unsafe`; stale `active_projects` entries cleared; consumes leftover
  `hermes:completion:*` keys (`scheduler.py:301-450`).
- **DAG dependency gating**: claim only when all dep tasks are terminal
  (`client.py:189-204, 441-467`).
- **Rate-limit pause**: connector paused (`daedalus:connector_paused:{cid}`) until reset,
  blocking all projects using it (`scheduler.py:644-651, 1265-1310`).
- Queue-depth Prometheus gauges per lane; periodic `git worktree prune`; anomaly scan
  (`scheduler.py:211-298`).
- Run kinds dispatched: `task`, `argus`, `planning`, `cleanup` (`scheduler.py:684-694`).

## PTY runner (Talos)

- Spawns agent CLIs in a real **PTY** (default 40×160, resizable) (`runner.py:405-412`).
- **Lifecycle controls** (signal whole process group):
  pause (SIGSTOP) / resume (SIGCONT) / interrupt (SIGINT) / kill (SIGTERM→SIGKILL, grace) /
  detach / inject (write stdin) / resize (`runner.py:287-305`, `pty.py:96-163`).
- Signals arriving before PTY ready are buffered in Redis (`runner.py:270-317`).
- **Timeouts**: wall-clock (default 60 min) + idle-output (default disabled)
  (`runner.py:491-550`).
- **Done-signal** detection: regex / tool_call / exit_code (`runner.py:485-522`).
- **Per-run git worktree** on `daedalus-run-<id>` branch off default; chowned to agent uid;
  compiled-artifact `.gitignore` auto-appended (`client.py:246-380`).
- **Auto-commit** on successful task completion (`runner.py:625-693`).
- **Live output** to Redis stream `pty:{run_id}` (maxlen ~10 k) (`runner.py:443-466`).
- **Transcript persistence** to S3/MinIO (local fallback) (`runner.py:711-730`).
- Per-connector **usage parser** → token_input/output + cost_usd_micros (`runner.py:704-709`).
- Claude trust-dialog pre-accept (flock-protected) (`claude_trust.py:26-64`).
- Graceful SIGTERM drain; `shutdown_killed` → `aborted_unsafe` (`runner.py:117-175`).

## Resource limits (cgroups v2)

- `daedalus.slice/run-<id>/` with `cpu.weight`, `memory.max`, `pids.max`
  (`cgroups.py:30-139`); no-ops on v1/macOS.

## Verification (Argus)

- **LLM verdict** (pass/partial/fail) + structured findings (severity blocker/major/minor;
  category bug/missing/regression/test/style; evidence) + optional suggested fix-task
  (`verifier.py:40-194`).
- Inputs: task criteria, `git diff <default>...HEAD` (artifact-excluded), verify-command
  output, exit code (`verifier.py:235-303`).
- **Phantom-commit guard**: deterministic fail if claimed SHA doesn't resolve
  (`scheduler.py:970-1018`).
- **No-progress detection**: SHA-256 diff-hash halting when consecutive attempts are
  identical (`scheduler.py:1087-1111`).
- **Analytical tasks**: empty diff acceptable when the agent's final report addresses the
  criteria (`verifier.py:49-88`).
- **Fix-loop spawning**: partial/fail → child fix-task (parent link, "fix-loop" tag);
  depth-capped by walking `parent_task_id`; chain root → "manual-review" once exceeded
  (`scheduler.py:1114-1140`).
- Deterministic fallback verdict when LLM unavailable (`verifier.py:314-336`).
