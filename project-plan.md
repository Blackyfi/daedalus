# DAEDALUS

**Distributed Agent Execution, Direction & Autonomous Lifecycle Unified System**

A self-hosted web platform for remotely orchestrating local AI coding agents (Claude Code, Codex, Qwen, custom shells) against project-scoped task graphs — with a single-runner queue, live terminal mirroring, an autonomous progress-verification loop, an idea-to-tasks intake, and hardened multi-factor access.

> Daedalus was the mythological master architect who built the Labyrinth and forged automatons (including Talos, the bronze guardian). The platform borrows the family of names for its subsystems: **Talos** runs the agent, **Argus** watches it, **Hermes** moves messages, **Mnemosyne** remembers, **Cerberus** guards the gate.

---

## 1. Executive Summary

Today, running a long coding agent like Claude Code means SSHing into a box, picking a directory, copy-pasting a prompt, watching a TUI, and manually scribbling down what got done. Multiply that across several projects and the friction is unmanageable.

Daedalus turns that experience into a single web UI. You log in once (with serious auth), pick or create a project, dump rough ideas into an Idea Box, let Daedalus draft a task list, approve it, and let a single shared agent runner work the queue task-by-task — with an automatic verification pass between each task that catches half-done work before the agent moves on. At any moment you can open the live terminal of the agent, intervene, pause, kill, or re-queue. You never have to open an editor.

The platform is moddable: any local agent CLI you can launch from a shell can be wired in via a JSON connector spec. It runs entirely in Docker on your own server, behind your existing internal CA.

---

## 2. Goals & Non-Goals

### 2.1 Goals

- **One agent at a time *per project*, projects in parallel.** Each project owns a single-runner queue: within a project, tasks serialize cleanly (same workspace, same worktree namespace, same Argus loop). *Across* projects, runs go in parallel up to a configurable global ceiling (`MAX_CONCURRENT_PROJECTS`). With a Claude Code Pro Max-x20 subscription and a safelock fallback, the host CPU/RAM ceiling is the only real cap — not API rate limits — so the platform exposes the parallelism instead of hiding it.
- **Project-first mental model.** Everything you do is anchored in a project: tasks, ideas, terminal sessions, history, artifacts, and progress reports.
- **Human-in-the-loop where it matters, autonomous where it doesn't.** Idea-to-task generation, progress verification, and "fix the bugs" loops happen automatically — but every state transition that changes scope (new tasks, new fixes, destructive operations) goes through an explicit confirmation in the UI.
- **Interactivity, not just streaming.** The browser doesn't just render the agent's stdout — it can type into the running PTY, send signals, pause, and resume.
- **Moddability via declarative connectors.** Adding a new agent (a new CLI, a new flag matrix, a new permission profile) is a JSON edit, not a code change.
- **Defense-in-depth security.** Strong password + email OTP + TOTP, mTLS via your existing internal CA, signed sessions, audit trails, sandboxed agent workspaces.
- **Single-binary-ish deploy.** `docker compose up` and the platform is live — including the agent runtime, queue, database, reverse proxy, and observability.

### 2.2 Non-Goals (v1)

- Multi-tenant SaaS. Daedalus is single-org / small-team self-hosted; you can have multiple users, but it's not designed for adversarial tenants.
- Multi-agent parallelism *within a single project*. By design, only one agent process runs per project at a time — projects share workspaces and Argus loops, and overlapping runs in the same project would race the worktree namespace and double-schedule verification. (Cross-project parallelism *is* a v1 goal, capped by `MAX_CONCURRENT_PROJECTS`.)
- Replacing your IDE. The platform is for orchestration and oversight; it doesn't try to be a code editor.
- Cloud agent gateways. Daedalus is for *local* agents on the same host; remote agent endpoints are a v2 extension.

---

## 3. Greek Naming Map (Subsystems)

| Codename       | Role                                                                 |
|----------------|----------------------------------------------------------------------|
| **Daedalus**   | The platform itself — orchestration, web UI, API gateway.            |
| **Talos**      | The agent runner. PTY supervision, lifecycle, signal handling.       |
| **Hermes**     | The queue / scheduler. Per-project FIFO with priority lanes; concurrent across projects up to a global cap. |
| **Argus**      | The progress verifier. Reads diffs/tests/logs, judges task done-ness.|
| **Mnemosyne**  | The memory layer — audit log, transcripts, task history, blob store. |
| **Cerberus**   | The auth gate — password, email OTP, TOTP, mTLS, session keys.       |
| **Hephaestus** | The build/test runner used by Argus to actually exercise code.       |
| **Iris**       | The realtime channel — WebSocket fan-out for terminal & progress.    |
| **Pythia**     | The subscription oracle — probes `claude /status`, caches plan tier + remaining quota for the UI. |

---

## 4. End-to-End User Flow

A typical day:

1. **Sign in** at `https://daedalus.your.lan`. Browser presents its client cert (mTLS via your internal CA). Cerberus also requires the master password, then a one-time email link valid 15 minutes (one-shot), then a TOTP code from Google Authenticator. Session cookie is issued — short-lived, sliding-expiration, bound to the cert fingerprint.
2. **Pick a project** from the project gallery, or create a new one (`name`, `description`, `git repo path on host`, optional `default agent connector`).
3. **Drop ideas into the Idea Box.** Free-form, single line or multi-line, no structure required. You can edit and re-order them.
4. **Hit "Generate Tasks from Ideas."** Daedalus enqueues a *planning task* on the next free runner cycle, which reads the project context (repo tree summary, README, existing task list, ideas) and proposes a structured task list. The proposal lands in a **Review** view; you can edit titles, descriptions, acceptance criteria, dependencies, priorities, and assigned connector. Confirm to commit them into the project task list.
5. **Start the runner.** Hermes picks the highest-priority unblocked task and hands it to Talos. Talos spawns the configured agent CLI (`claude`, `claude-multi`, `qwen`, etc.) with the task description + acceptance criteria as the initial prompt and the project's mounted workspace as cwd.
6. **Watch / intervene.** The Project view shows the task board with live progress. Clicking the running task drops you into the terminal mirror — same characters the agent sees, with full input. You can type, send Ctrl+C, pause (SIGSTOP), resume (SIGCONT), kill, or re-enqueue.
7. **Agent declares done.** When the agent emits its done signal (configurable per connector — could be a token like `<<TASK_DONE>>`, a CLI exit, or a structured tool call), Talos *does not* advance the queue. Instead it enqueues a high-priority **Argus verification job** for the same task.
8. **Argus runs.** Argus is itself an agent invocation, but with a stripped-down read-only profile and a verification prompt: "Given task X, verify it is actually completed in the working tree. Run the tests/lints/build steps in the connector's `verify` command. Inspect the git diff. Report `pass`, `partial`, or `fail` with a structured findings list." Argus also examines the test runner output (Hephaestus actually executes the verification commands).
9. **Branch on verdict.**
   - `pass` → task moves to **Done**, the project view updates, Hermes advances to the next task.
   - `partial` or `fail` → task moves to **Needs Fixes**, Argus's findings are attached, and a **Fix Task** is auto-created and queued at high priority. The original agent (or a fresh one, configurable) picks it up.
10. **You stay informed.** The project view shows percent complete, current task, last verification verdict, and a one-click button on any failed task: *"Send back to agent."*

You never opened a terminal of your own.

---

## 5. Architecture Overview

```
                ┌─────────────────────────────────────────────────┐
                │                Browser (mTLS)                   │
                │      React SPA  +  xterm.js PTY mirror          │
                └──────────────┬──────────────────────────────────┘
                               │ HTTPS + WSS (signed cookies)
                ┌──────────────▼──────────────┐
                │    Caddy / Nginx (mTLS)     │
                │  cert-pin to internal CA    │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐         ┌────────────────────┐
                │   Daedalus API (FastAPI)    │◄───────►│   Cerberus (Auth)  │
                │   REST + WebSocket gateway  │         │  pwd/OTP/TOTP svc  │
                └──┬───────────────────────┬──┘         └────────────────────┘
                   │                       │
        ┌──────────▼─────────┐    ┌────────▼─────────┐
        │  Hermes (queue)    │    │  Mnemosyne (DB)  │
        │  Redis / RQ + DAG  │    │  Postgres + S3-c │
        └──────────┬─────────┘    └──────────────────┘
                   │
        ┌──────────▼─────────────────────────────┐
        │     Talos (single agent supervisor)    │
        │   pty fork, signal control, streaming  │
        └──────────┬─────────────────────────────┘
                   │ spawns one of:
        ┌──────────┴──────────┬───────────┬─────────────┐
        │   claude (CLI)      │ claude-mu │ qwen        │ ...moddable
        └─────────────────────┴───────────┴─────────────┘

       ┌────────────────────────────────────────────┐
       │  Argus (verifier) — same Talos pipe, but  │
       │  read-only profile + Hephaestus runners    │
       └────────────────────────────────────────────┘
```

Every box is its own container in `docker-compose`, except Talos and the agent CLIs which share a container so that PTY semantics are clean. The shared volume between Talos and the API is the only place agent stdout/stderr leaves the runner.

---

## 6. Core Components

### 6.1 Daedalus API

The HTTP + WebSocket front door. Stateless. Owns:

- REST endpoints for projects, tasks, ideas, connectors, runs, history.
- WebSocket endpoints for terminal streams, progress events, queue updates.
- The session/auth middleware (delegates to Cerberus).
- Authorization checks (every action is logged with the user, IP, cert fingerprint).

Tech: **FastAPI** (Python) for fast iteration and easy asyncio integration with the PTY pipes. Pydantic models define the shared schema with the React frontend (auto-generated TS types via `openapi-typescript`).

### 6.2 Talos — Agent Runner

The single most safety-critical component. Responsibilities:

- Spawn an agent CLI inside a controlled subprocess via a real PTY (`ptyprocess` / `pexpect` in Python, or a tiny Go runner — see §16 for the trade-off).
- Stream stdout/stderr (combined into the PTY master) to a Redis stream (`pty:run:<run_id>`).
- Accept stdin from the WebSocket gateway (typed keystrokes, control codes).
- Honor lifecycle commands from Hermes: `pause` (SIGSTOP), `resume` (SIGCONT), `interrupt` (SIGINT), `kill` (SIGTERM → SIGKILL after grace), `detach`.
- Enforce per-run resource limits via cgroups v2: CPU shares, RAM ceiling, PIDs cap, wall-clock timeout, idle-output timeout.
- Mount the project workspace read-write (or read-only for Argus runs) into the agent's cwd.
- Mount secrets (API keys for the agent CLIs) from a Docker secret store, never as env vars in `docker inspect` output.
- Capture exit code + final transcript and ship them to Mnemosyne.

Talos is **multi-run aware**: one Talos process supervises up to `MAX_CONCURRENT_PROJECTS` concurrent runs (one per project). Each run has its own `RunContext` (PTY session, transcript buffer, cgroup, signal-completion flags) keyed by `run_id` in `runner.contexts`. Lifecycle signals (`pause`/`resume`/`interrupt`/`kill`/`detach`/`inject`/`resize`) route by `run_id`, not by a global "current run." If Talos crashes, every in-flight run's per-run `hermes:lock:<run_id>` and per-project `hermes:project_lease:<project_id>` keys expire by TTL; Hermes's orphan reclaim then transitions any DB row still in `running`/`claimed` without a live lock to `aborted_unsafe`.

### 6.3 Hermes — Queue & Scheduler

Hermes is a small Python scheduler over Redis with per-project concurrency.

**Queues.** Three lane LISTs (`hermes:queue:urgent`, `:default`, `:bg`). Within a lane, FIFO; across lanes, strict priority. `urgent` carries fix-loops, user interventions, and Argus verifications; `default` carries normal task runs; `bg` carries planning and scheduled audits.

**Per-project lease.** A run is "claimable" only if its project's lease is free. Lease key: `hermes:project_lease:<project_id>` → `<run_id>`, TTL = `connector.resource_limits.wall_clock_minutes + 5min`, refreshed every 60s by the dispatcher while the run is in flight. Membership in `hermes:active_projects` (a Redis SET) gives O(1) cap checks.

**Concurrency.** `MAX_CONCURRENT_PROJECTS` worker coroutines run in parallel inside the scheduler process. Each worker:

```
loop forever:
    job = atomic_claim_next_idle_project()   # Lua script, see §6.3.1
    if job is None:
        await sleep(POLL_INTERVAL); continue
    dispatch(job)               # publishes hermes:signal:<run_id> to Talos
    await wait_for_completion(job)
    release_lease(job.project_id)
```

A separate **bookkeeper coroutine** runs orphan reclaim, lane-depth metrics, and the Pythia subscription refresh tick (§6.10).

**State machine.** `queued → claimed → running → (completed | failed | cancelled | aborted_unsafe)`. Persisted to Redis AOF and mirrored to Postgres for durability. DAG: a task with `depends_on: [task_ids]` is skipped during the LRANGE scan until every dep run is in a terminal state.

#### 6.3.1 The atomic claim algorithm

Naively popping the queue head and pushing it back if the project is busy creates churn (every worker rotates the queue). Instead, Hermes scans queue contents with `LRANGE 0 -1` per lane, picks the first entry whose project is idle and whose deps are met, and atomically claims it via a Lua script:

```
KEYS = [queue_list, project_lease_key, active_projects_set]
ARGV = [run_id, project_id, lease_ttl_seconds, max_concurrent_projects, payload_json]

if SCARD(active_projects) >= max_concurrent_projects:
    return nil                          -- global cap reached
if EXISTS(project_lease_key):
    return nil                          -- project already busy
if LREM(queue_list, 1, payload_json) == 0:
    return nil                          -- another worker claimed it first
SET project_lease_key run_id EX lease_ttl_seconds
SADD active_projects project_id
return payload_json
```

This is the single source of truth for "did I get this run?" — no double-dispatch is possible.

#### 6.3.2 Edge cases — explicitly handled

| # | Case | Handling |
|---|------|----------|
| 1 | Two workers race on the same queue entry | Lua atomicity. Only one's `LREM` returns 1; the other gets nil and re-scans. |
| 2 | Every queue head belongs to a busy project | Workers `LRANGE`-scan past blocked entries; no pop-and-repush churn. |
| 3 | Hermes process crash mid-run | Lease TTL expires; on restart, orphan reclaim transitions stranded `running`/`claimed` rows to `aborted_unsafe` and SREMs their project from `active_projects`. |
| 4 | Run exceeds expected wall-clock | Dispatcher refreshes the lease every 60s while waiting; if Talos itself enforces wall-clock and kills, completion path releases the lease normally. |
| 5 | Argus verification of just-finished task | Argus run is enqueued *after* the parent task releases the lease. The Argus run takes a fresh lease on the same project. Sequential, never overlaps. |
| 6 | Planning run for a project with active task | Planning takes the project lease too — they're brief but can mutate task state, so serialising them with task runs avoids races. |
| 7 | DAG dependency unmet | Skipped during the scan (no dequeue), no churn. Re-evaluated on the next tick after a dep completes. |
| 8 | Connector forks sub-agents internally (`claude-multi`) | Out of scope for the platform lease — what the connector does *inside* its PTY is its business. The platform-level promise is "one Daedalus-managed run per project." |
| 9 | Project deletion during a run | Cascade-deletes the run row; lease key TTLs out. The next `release_lease` is a no-op. |
| 10 | Redis disconnect during dispatch | Lease TTL covers it. On reconnect, dispatcher detects the missing lease, marks the run `aborted_unsafe` if completion didn't arrive. |
| 11 | Global cap raised at runtime | New workers spawn lazily on the next tick; existing in-flight runs are unaffected. Lowering the cap quiesces by attrition (running runs finish; new ones blocked). |
| 12 | Talos restarts while runs are in flight | Talos's own startup orphan-recovery (§6.2) sends `aborted_unsafe` completions for every run that was active when it died. Hermes processes those completions normally. |

### 6.4 Argus — Verification Loop

Argus is not a separate AI model; it's a *role* run on top of the same connector framework with a special prompt and a restricted profile. It is given:

- The task description and acceptance criteria.
- The project's `verify` commands from the connector spec (e.g. `pytest -q`, `npm test`, `cargo build`).
- A read-only mount of the workspace.
- The git diff since the task started (Daedalus auto-creates a worktree per task — see §11.4).

Argus produces a structured JSON verdict:

```json
{
  "verdict": "pass | partial | fail",
  "summary": "...",
  "findings": [
    {"severity": "blocker|major|minor", "category": "bug|missing|regression|test|style", "description": "...", "evidence": "..."}
  ],
  "suggested_fix_task": {
    "title": "...",
    "description": "...",
    "acceptance_criteria": "..."
  }
}
```

Daedalus parses the JSON (rejecting and re-prompting on parse failure), persists it, and either marks the task `done` or auto-creates the fix task at `urgent` priority.

### 6.5 Mnemosyne — Persistence

- **Postgres** for relational state: users, projects, tasks, ideas, runs, connectors, audit log.
- **Object store** (MinIO container or just a mounted volume) for blobs: full PTY transcripts (compressed), Argus reports, generated artifacts, snapshot tarballs.
- **Redis** for ephemeral pub/sub and the live PTY stream (transcripts are mirrored to the object store on run completion).
- Hot/cold split: live runs in Redis (last 24h), historical in object store.

### 6.6 Cerberus — Auth & Identity

See §10.

### 6.7 Iris — Realtime Channel

A small fan-out service that subscribes to Redis streams and pushes to browser WebSockets, multiplexing channels per topic (`pty:<run_id>`, `progress:<project_id>`, `queue:status`). Allows multiple browser tabs / multiple devices to attach to the same live run.

### 6.10 Pythia — Subscription Oracle

Pythia answers a single question: *"How much agent capacity does this host have left right now?"* For Claude Code that means: which OAuth plan is the operator on (Pro / Pro Max / Max 5x / Max 20x), how much of the weekly + 5-hour windows is consumed, when do they reset.

**Why a separate subsystem.** The answer can't be derived from anything Daedalus already knows — it lives in Anthropic's quota service and is exposed only through `claude /status`. The probe needs the *same* `~/.claude` auth state the connectors use, so it has to run inside the Talos container (where the OAuth tokens are mounted).

**Architecture.**

```
Talos (every 10 min, on boot, and on demand)
    │
    ├── claude --print "/status"      # 10-second timeout
    │       │
    │       ▼
    ├── parse plan + usage + reset times    (pythia.parse_status)
    │
    └── SET daedalus:subscription:claude  {json}  EX 1800

Daedalus API
    └── GET /api/v1/system/subscription
              ▲
              └── reads from Redis cache; never blocks on the CLI
```

**Parser tolerance.** `claude /status` output isn't a stable JSON contract — it's human-readable text that has changed across versions. Pythia uses a layered parser: structured JSON if available; otherwise a regex pass that pulls plan tier, usage percents, and reset countdowns; falling back to a raw-text passthrough so the UI can at least display *something*. Parse failures don't break the cache — they emit a `kind: "unknown"` entry with the raw output for diagnosis.

**Edge cases handled.**

| # | Case | Handling |
|---|------|----------|
| 1 | `claude` CLI not on `$PATH` | Probe records `kind: "cli_missing"`; UI shows "Subscription: unknown — claude CLI not detected." |
| 2 | OAuth not authenticated | Probe records `kind: "auth_required"`; UI shows a "Run `claude /login`" hint. |
| 3 | CLI hangs | 10-second hard timeout; probe records `kind: "timeout"`. |
| 4 | Multiple OAuth accounts (different `~/.claude` per connector) | v1 probes the default account only — documented limitation. v1.x extends to per-connector probes. |
| 5 | Output format changes upstream | Parser falls back to `kind: "unparsed"`, raw text passthrough. UI shows raw text in a `<pre>`. |
| 6 | Quota at 100% | UI banner turns red, "Subscription exhausted — runs may fail." |
| 7 | Probe runs while Talos is busy | Probe is a fast standalone subprocess (no PTY), runs concurrently with task runs. |

---

## 7. Agent Connector System (Moddability)

An "agent connector" is a JSON file that tells Talos how to run a specific CLI. Drop a new file into `/etc/daedalus/connectors/` (or paste it into the UI) and the connector is available immediately.

### 7.1 Schema (JSON Schema, summarized)

```json
{
  "id": "claude-code-confirm",
  "display_name": "Claude Code (confirm before mutating)",
  "description": "Standard Claude Code with permission prompts on file mods and shell.",
  "command": "claude",
  "args": ["--permission-mode=ask"],
  "env": {
    "ANTHROPIC_API_KEY": "{{secret:anthropic_key}}"
  },
  "workdir": "{{project.workspace_path}}",
  "permission_profile": "confirm",
  "input_format": {
    "kind": "stdin_prompt",
    "template": "Task: {{task.title}}\n\n{{task.description}}\n\nAcceptance criteria:\n{{task.acceptance_criteria}}\n"
  },
  "done_signal": {
    "kind": "regex",
    "pattern": "<<TASK_DONE>>|^DONE$"
  },
  "exit_on_done": true,
  "verify_commands": [
    "git status --porcelain",
    "pytest -q",
    "ruff check ."
  ],
  "argus_profile": {
    "command": "claude",
    "args": ["--permission-mode=read-only", "--no-tools=write,bash"],
    "workdir_readonly": true
  },
  "resource_limits": {
    "cpu_shares": 1024,
    "memory_mb": 4096,
    "wall_clock_minutes": 60,
    "idle_output_minutes": 10
  },
  "interrupt": {
    "soft": "SIGINT",
    "hard": "SIGTERM",
    "kill_grace_seconds": 5
  },
  "tags": ["coding", "anthropic", "safe"]
}
```

### 7.2 Permission Profiles

Connectors declare a profile. Two ship by default:

- **`confirm`** — wraps the agent CLI in flags that force human confirmation on file writes and shell commands. Daedalus surfaces the confirm prompts in the terminal mirror; you can answer in-browser.
- **`yolo`** — unrestricted, full-permissions agent (e.g. `claude --dangerously-skip-permissions`, codex full). Daedalus enforces extra guardrails around `yolo`: mandatory git auto-snapshot of the workspace before the run; auto-revert button in the UI; banner warning; audit log highlight; optional block on production-tagged projects.

You can register variants of the same CLI with different profiles (e.g. `claude-code-confirm` and `claude-code-yolo`), and pick per-task which profile runs.

### 7.3 Templating

Connector fields support a small templating language with three namespaces:

- `{{task.*}}` — task fields.
- `{{project.*}}` — project fields, including `workspace_path`, `git_branch`.
- `{{secret:name}}` — looked up from the secrets vault, never logged, redacted in transcripts.

### 7.4 Validation & Hot-reload

Connectors are validated against the JSON Schema on save. Invalid connectors are flagged but don't break the platform. Existing runs aren't affected by edits — runs snapshot the connector spec at claim time.

### 7.5 Built-in connectors shipped with v1

- `claude-code-confirm`
- `claude-code-yolo`
- `claude-multi-confirm` (your custom multi-agent variant — connector spec lets you declare its special flags / env)
- `claude-multi-yolo`
- `qwen-coder-confirm`
- `qwen-coder-yolo`
- `codex-confirm`, `codex-yolo`

---

## 8. Project Management Module

### 8.1 Data Model (Conceptual)

```
User ─┐
      └─ has many ─ Project
                     ├─ has many ─ Task
                     │              ├─ has many ─ Run (one per agent execution)
                     │              ├─ has many ─ ArgusReport
                     │              └─ depends_on ─ Task[]
                     ├─ has many ─ Idea
                     ├─ has many ─ Note (long-form context)
                     ├─ has one ─ GitWorkspace (path, default branch)
                     └─ has many ─ Snapshot (pre-yolo tarballs / git tags)
```

### 8.2 Task Fields

Title, description, acceptance criteria (markdown), priority (P0–P3), status (`backlog | ready | in_progress | verifying | needs_fixes | done | cancelled`), assigned connector ID, depends-on list, estimated wall time, tags, attachments, parent (for fix-tasks → original).

### 8.3 Idea Box

Lightweight: each idea is just `text + created_at + tags`. UI is closer to a sticky-note board than a task list. Operations:

- Add (single line or multi-line modal)
- Edit / delete
- Re-order via drag
- **"Plan from ideas"** button → enqueues a planning job (background priority).

### 8.4 Planning Job

Reads project context + ideas → produces a draft task list as JSON:

```json
{
  "proposed_tasks": [
    {"title": "...", "description": "...", "acceptance_criteria": "...",
     "priority": "P1", "depends_on": [], "suggested_connector": "claude-code-confirm"}
  ],
  "rationale": "..."
}
```

The proposal lands in a **Plan Review** modal where every field is editable inline. You confirm to commit; ideas that were "consumed" can optionally be archived.

### 8.5 Project Dashboard

Three panels:

- **Task board** (Kanban-lite columns: Backlog / Ready / Running / Needs Fixes / Done) with live updates.
- **Live runner panel** showing the currently active task *for this project*, the agent connector, elapsed time, last 20 lines of output (a "preview" — full terminal opens on click), and quick-action buttons (pause, resume, intervene, kill, requeue).
- **Verification feed** — a chronological list of Argus reports with verdicts and findings.

### 8.5.1 Global Runner Bar

Above all project pages, the Shell header carries a runner bar:

- **Slot meter**: `3 / 4 projects running` (current active count vs. `MAX_CONCURRENT_PROJECTS`).
- **Per-project chips**: for each active run, a chip showing project name + elapsed time, clickable to jump straight to that project's RunPanel.
- **Pythia chip**: subscription tier + colour-coded usage indicator (green < 60%, amber 60–85%, red > 85%); click for the full quota popover.

The Project List page also gains an **active-run badge** per project tile (`▶ Task: {title} • 3m`) so the operator can see at a glance which projects are working without opening each one.

### 8.6 History & Replay

Every run's full transcript is browsable in Mnemosyne. The UI offers a transcript viewer with:

- Line-numbered scrollback
- Copy buttons
- "Re-run this task" (clones the task + its connector snapshot)
- Diff view of the workspace at the start vs. end of the run (git-based)

---

## 9. Workflow Engine — The Verification Loop

This is the heart of the "agent doesn't move on until it's actually done" guarantee.

```
[Task picked up by Talos]
        │
        ▼
[Agent runs to "done signal" or wall-clock]
        │
        ▼
[Talos quiesces the agent, snapshots the workspace via git]
        │
        ▼
[Argus job enqueued at urgent priority]
        │
        ▼
[Argus reads diff + runs verify_commands via Hephaestus]
        │
        ▼
[Argus emits structured verdict JSON]
        │
        ├── pass    → mark task done; advance queue
        ├── partial → create fix-task (urgent); re-queue same task as parent;
        │              optionally also auto-run the fix immediately
        └── fail    → same as partial, but block dependent tasks until resolved
```

Knobs (per project or per task):

- **Max fix loops**: e.g. 3. After that, escalate to human review and stop.
- **Auto-run fix or wait for confirmation**: default = wait, because "fail loops" can chew tokens.
- **Argus connector**: which connector verifies (often a cheaper / faster model).

Anti-runaway protections:

- Hard wall-clock per task.
- Hard token-spend cap per task (if connector exposes usage).
- Detection of "no progress" on retry (same diff, same failures) → auto-pause for review.

---

## 10. Security Architecture

### 10.1 Threat Model

- **External attacker** on the LAN or with a stolen device. Mitigated by mTLS + 3FA.
- **Compromised agent run** that tries to exfiltrate or destroy data. Mitigated by sandboxing + read-only mounts where possible + per-run secrets + workspace snapshots.
- **Malicious connector spec** (someone tricks you into installing a bad JSON). Mitigated by schema validation + command allowlist + signed connectors (optional, see §10.6).
- **Replay / session hijack**. Mitigated by short TTLs + cert-bound sessions + rotating tokens.
- **Insider misuse**. Mitigated by full audit logging, including every keystroke piped to the PTY.

### 10.2 Authentication (Cerberus)

Three factors. All three are required for fresh logins; only TOTP (or a hardware-key alternative) is required for re-auth within a sliding session window.

1. **Strong password.**
   - Stored as Argon2id (default params: m=64MiB, t=3, p=4). Pepper from a Docker secret.
   - Enforced minimum: 14 chars, ≥1 of each class, zxcvbn score ≥ 4, breach-list check (HIBP k-anonymity, run locally on a downloaded dump if you don't want to call out — configurable).
   - Lockout: 5 failures → 15-min lockout per user. 25 failures from an IP → 1-hour IP ban.

2. **Email OTP — single-use, 15-minute TTL.**
   - Triggered after password success.
   - 8-digit code + a magic link (only one needs to be used; using one invalidates the other).
   - Code is HMAC'd in the DB; the email contains the cleartext.
   - Bound to the originating browser fingerprint + IP class — clicking the link from a different network requires the code path instead.
   - SMTP via a configurable relay; deliverability is on you.

3. **TOTP (RFC 6238).**
   - Standard 30-second / 6-digit, SHA-1 (broadest compatibility) or SHA-256 (stricter).
   - Enrollment via QR code; backup recovery codes (10, single-use, hashed).
   - Optional WebAuthn / hardware key as a TOTP replacement (recommended; ship as v1.1).

After all three: a session cookie is set (`HttpOnly`, `Secure`, `SameSite=Strict`, signed JWT or opaque ID), bound to the client cert fingerprint. Sliding window: 30-minute idle → re-prompt for TOTP only. Hard cap: 12 hours → full re-auth.

### 10.3 Transport — mTLS with Your Existing CA

- The reverse proxy (Caddy or Nginx) is configured with `ssl_client_certificate` / `ca_file` pointing at your internal CA bundle.
- `ssl_verify_client on` (Nginx) / `client_auth.mode require_and_verify` (Caddy) — connections without a valid client cert are rejected at TLS handshake; the API never sees them.
- The cert's CN/SAN is forwarded as a header (`X-Client-Cert-CN`) and used as the *first* identity check before username; mismatched cert + user pair = immediate 403 + alert.
- Cert revocation: configure a CRL or OCSP responder pointing at your CA.
- Renewal: documented; out of scope for the platform itself.

### 10.4 Authorization

Roles: `owner`, `member`, `viewer`. (Single-user setups just have one owner.) Every API call goes through a policy decision; project-level scoping ensures a member can only touch their own projects unless granted.

### 10.5 Secrets

- Docker secrets at runtime; never baked into images.
- Per-connector secret references (`{{secret:NAME}}`) resolved at run-claim time.
- Transcripts are scrubbed of any string equal to a secret value before storage, with a regex backstop for common patterns (Bearer tokens, AWS keys, etc.).

### 10.6 Connector Signing (optional, v1.1)

You can require connectors to be signed with a key you control. Unsigned connectors require an extra confirmation step on install. Useful if multiple admins.

### 10.7 Audit Log

Append-only Postgres table, mirrored to a write-only file on a different volume. Every action — login, project create, task edit, run start/stop, terminal keystrokes, secret access — is logged with `who`, `what`, `when`, `where (IP, cert)`, `before`, `after`. Browsable in the UI by owner role only. Export to syslog via a small forwarder.

### 10.8 Network Egress from Agent Runs

The agent containers run on a separate Docker network with egress filtering: only the API endpoints of the model providers (Anthropic, OpenAI, Qwen) and the project's git remotes are allowed by default. Configurable per project.

### 10.9 Workspace Isolation

Each project's workspace is a directory mounted into Talos's container only. Hard-link / symlink escapes are prevented by mounting `nosymfollow` where supported; otherwise by a pre-run check that the workspace is canonicalized and inside the configured root.

---

## 11. Live Terminal Mirroring & Interaction

### 11.1 Why a true PTY matters

Claude Code's TUI uses ANSI escape sequences, alternate screen, cursor positioning. A naive pipe of stdout breaks the rendering. Talos allocates a real pseudo-terminal:

- The agent's `stdout`/`stderr` are joined onto the PTY master.
- The PTY size is set from the browser's xterm.js size at attach time and updates on resize.
- Bytes flow both ways: agent → master → Redis stream → Iris → WebSocket → xterm.js; keystrokes → WebSocket → Iris → Redis → Talos → PTY master → agent.

### 11.2 Multi-attach

Multiple browser sessions can attach to the same run. Only one is "input-capable" at a time; others are read-only by default with a "take input" button that does an explicit hand-off (with a confirmation toast on the previous holder).

### 11.3 Lifecycle controls in the UI

- **Pause** → SIGSTOP. Banner shows "paused"; the agent is frozen mid-instruction. Useful when you spot it doing the wrong thing and want to think.
- **Resume** → SIGCONT.
- **Interrupt** → SIGINT (e.g., to cancel a long sub-command without killing the agent).
- **Inject prompt** → types a string into the PTY. Useful to redirect the agent without killing it.
- **Kill** → SIGTERM, then SIGKILL after grace.
- **Detach without killing** → leaves the run going in the background.

### 11.4 Per-run git worktree

When a run starts, Daedalus creates a fresh `git worktree` from the project's default branch into a temporary path: `workspaces/<project>/runs/<run_id>/`. This becomes the agent's cwd. Benefits:

- Trivial diff for Argus.
- Painless rollback (delete the worktree).
- Concurrent reads of the original branch unaffected.
- On `pass`, Daedalus offers a one-click "merge to main" (rebase + fast-forward) or "create PR" if there's a configured remote.

---

## 12. API Surface (Sketch)

REST paths under `/api/v1`:

- `POST /auth/password` → step 1 of login.
- `POST /auth/email-otp` → step 2.
- `POST /auth/totp` → step 3, returns session cookie.
- `POST /auth/logout`.
- `GET /projects`, `POST /projects`, `GET/PATCH/DELETE /projects/:id`.
- `GET /projects/:id/tasks`, `POST .../tasks`, `PATCH .../tasks/:tid`, ...
- `POST /projects/:id/ideas`, `GET/PATCH/DELETE` likewise.
- `POST /projects/:id/plan` → enqueue a planning job.
- `POST /projects/:id/plan/:planId/confirm` → commit proposed tasks.
- `POST /tasks/:tid/run` → enqueue.
- `POST /runs/:rid/pause | /resume | /interrupt | /kill | /detach`.
- `POST /runs/:rid/inject` (body: `{text}`).
- `GET /runs/:rid/transcript`.
- `GET /runs/:rid/argus`.
- `GET /connectors`, `POST /connectors`, `PATCH/DELETE /connectors/:id`.
- `GET /audit` (owner only).

WebSocket paths:

- `WSS /ws/runs/:rid/pty` — bidirectional PTY stream.
- `WSS /ws/projects/:id/events` — board updates, queue updates, verdicts.
- `WSS /ws/queue` — global queue state.

Every WebSocket inherits the HTTP session.

---

## 13. Tech Stack (Recommended)

- **Frontend**: React + TypeScript + Vite, Tailwind, shadcn/ui, **xterm.js** for the terminal, **TanStack Query** for data, **Zustand** for local UI state. WebSocket via `partysocket` for resilient reconnects.
- **Backend**: Python 3.12 + **FastAPI**, async everywhere. **Pydantic v2** for schemas. **SQLAlchemy 2.x** + **Alembic** for the ORM/migrations.
- **Queue**: **Redis** + a thin custom scheduler (or **Arq** if you want it off-the-shelf).
- **DB**: **Postgres 16**.
- **Object store**: **MinIO** (S3-compatible) or local FS.
- **Reverse proxy**: **Caddy** (simpler mTLS config) or **Nginx**.
- **PTY**: Python `ptyprocess`. Alternative: a small Go sidecar for the runner if Python's GIL becomes a bottleneck on heavy streams (it shouldn't at v1 scale).
- **Auth**: hand-rolled on top of Argon2id + `pyotp` for TOTP + a tiny SMTP sender.
- **Observability**: **OpenTelemetry** traces, **Loki** logs, **Prometheus** metrics, **Grafana** dashboards. Bundled in the compose file as an optional profile.
- **Container orchestration**: **docker compose** for v1. Kubernetes manifests in v1.x if needed.

---

## 14. Deployment

### 14.1 Docker Compose Topology

Services:

- `caddy` (or `nginx`) — public-facing on 443; mTLS termination.
- `api` — Daedalus FastAPI.
- `iris` — websocket fan-out.
- `talos` — single agent runner; privileged enough to fork PTYs and apply cgroups; otherwise minimal.
- `hermes` — scheduler worker.
- `argus-worker` — same image as Talos but configured for verification jobs.
- `postgres`, `redis`, `minio`.
- `smtp-relay` (or external relay).
- Optional: `prometheus`, `grafana`, `loki`, `otel-collector`.

Volumes:

- `daedalus-data` for Postgres.
- `daedalus-objects` for MinIO.
- `daedalus-workspaces` mounted into Talos at `/workspaces` — your project repos live here.
- `daedalus-secrets` mounted read-only into containers that need them.

Networks:

- `frontnet` — caddy + api + iris.
- `backnet` — api + workers + db + redis + minio.
- `agentnet` — talos + argus-worker, with egress filter (see §10.8).

### 14.2 First-run Setup

A `daedalus init` CLI inside the API container that:

1. Creates the owner account (interactive password + immediate TOTP enrollment).
2. Mounts your CA bundle.
3. Imports the default connector pack.
4. Optional: scans `/workspaces` for existing git repos and offers to register them as projects.

### 14.3 Backup & Recovery

- Postgres: nightly `pg_dump` to MinIO, retained 30 days.
- Object store: lifecycle policy + offsite sync (rclone) — configurable.
- Workspaces: rely on the project repos' own `git push` to a remote you trust.
- Pre-yolo snapshots: created as git tags (`daedalus-snap/<run_id>`) plus a tarball of any non-tracked files.

### 14.4 Upgrade Path

- All schema changes via Alembic migrations.
- Connectors are forward-compatible by virtue of schema versioning (`schema_version` field).
- Blue-green for the API container; the runner container drains by completing the active run before swap.

---

## 15. Phased Roadmap

**Phase 0 — Foundations (1–2 weeks)**
Repo scaffolding, CI, Docker Compose skeleton, mTLS termination, password+TOTP+email OTP working end-to-end, basic projects/tasks CRUD, Postgres schema v1.

**Phase 1 — Single-agent execution MVP (2–3 weeks)**
Talos PTY runner, Hermes single-lane queue, one connector (`claude-code-confirm`), terminal mirror in xterm.js, manual task creation and execution, run history.

**Phase 2 — Connectors framework (1 week)**
JSON-schema connectors, hot-reload, default pack (claude-multi, qwen, codex, yolo variants), per-task connector selection.

**Phase 3 — Verification loop (2 weeks)**
Argus verifier, Hephaestus runner integration, structured verdicts, fix-task auto-creation, configurable max-fix-loops, anti-runaway guards.

**Phase 4 — Idea Box & Planning (1 week)**
Idea CRUD, planning job, plan review modal, commit-to-tasks flow.

**Phase 5 — Polish & ops (2 weeks)**
Audit log UI, observability stack, backups, snapshot/rollback for yolo runs, transcript viewer with diff, multi-attach terminal, recovery codes for TOTP.

**Phase 6 — Hardening (ongoing)**
Connector signing, WebAuthn, egress filter wizard, anomaly detection on audit log, per-project token spend caps.

---

## 16. Open Decisions & Trade-offs

Each one is a place where the spec could go either way; my recommendation is in **bold**, but flag them for review.

- **PTY runner language: Python `ptyprocess` vs. small Go sidecar.** Python keeps the stack uniform and is fine at single-runner scale. **Recommendation: Python for v1; carve out a Go runner only if profiling forces it.**
- **Argus verdict format: free-text vs. strict JSON.** Free-text is easier to obtain from any model; strict JSON is robust. **Recommendation: strict JSON with a parse-retry loop, plus a fallback regex extractor.**
- **Auto-run fix vs. wait-for-confirm by default.** Auto-run is faster but burns tokens on bad loops. **Recommendation: confirm-first as default; per-project toggle to auto-run with max-loop cap.**
- **Concurrency model: per-project runner with a global cap (decided).** v1 ships per-project single-runner (one Daedalus-managed run per project) with `MAX_CONCURRENT_PROJECTS` projects in flight at once. Within a connector that forks sub-agents (`claude-multi`), the platform-level promise is "one Daedalus-managed run per project" — the connector's internal fan-out is its own business. With Pro Max-x20 + a safelock fallback, the meaningful ceiling is host CPU/RAM, not API quota. See §6.3.1 for the atomic claim algorithm.
- **Where to draw the line between Daedalus and your IDE.** No editor in v1; transcripts and diffs only. Adding a Monaco-based file viewer is a v1.x stretch goal.
- **Multi-user vs. single-user.** v1 supports multi-user with simple roles; project sharing is per-user. SSO/OIDC is v2.
- **Cost / token tracking.** Connectors can declare a `usage_parser` that pulls token counts from the agent's output. v1 ships parsers for Claude Code and OpenAI Codex; users can add others.
- **Where the agent's "done signal" comes from.** Three options: regex on output, exit code, structured tool call. v1 supports all three; connectors pick. **Recommendation: prefer structured tool call → exit code → regex, in that order.**

---

## 17. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Yolo agent destroys workspace | High | Mandatory git snapshot pre-run; one-click rollback; banner; audit highlight. |
| Runaway fix loop burns tokens | Medium | Hard loop cap; "no-progress" detection (same diff/test results); per-task token cap. |
| PTY stream lag on slow link | Medium | Backpressure-aware streaming; Iris drops middle frames if the client is far behind, never start/end. |
| Session hijack | High | mTLS + cert-bound cookie + short TTL + audit-log alerts on cert mismatch. |
| Connector JSON injection (template escape) | Medium | Render template only with allow-listed namespaces; reject any direct shell metacharacters in `command`/`args`. |
| Lost run on Talos crash | Low | Run lease + on-restart reconciliation; mark orphaned runs `aborted_unsafe`. |
| Email OTP delivery failure | Medium | Backup TOTP-only re-auth path after first 3FA; recovery codes. |
| Disk fills with transcripts | Medium | Compression + lifecycle policy + per-project quotas + UI warnings. |
| User locks themselves out | Medium | One-time recovery codes generated at TOTP enrollment; offline `daedalus reset-totp` CLI usable on the host. |

---

## 18. What Was Missing in the Original Brief (and Now Filled In)

These weren't in your prompt but you'll want them. Each is reflected in the body above.

- **Per-task git worktrees** for clean diffs and trivial rollback (§11.4).
- **Pre-yolo snapshots** as git tags + tarball, with one-click rollback (§14.3).
- **Anti-runaway protections** on the verification loop: max fix loops, no-progress detection, token/wall-clock caps (§9, §17).
- **Audit log** covering keystrokes injected via the UI as well as every state change (§10.7).
- **Egress filtering** for the agent network (§10.8).
- **Multi-attach terminal** with explicit input hand-off (§11.2).
- **Recovery codes** + offline TOTP reset CLI in case you lose your phone (§10.2, §17).
- **Connector schema versioning + spec snapshot at run claim** so editing a connector mid-run doesn't break in-flight runs (§7.4).
- **Cost/token tracking** parsers per connector (§16).
- **Backup story** for DB, objects, and workspaces (§14.3).
- **Three-tier priority queue** so user interventions, fix-loops, and planning don't starve real work (§6.3).
- **Structured JSON Argus verdicts** with a fallback parser (§6.4, §16).
- **Detach-without-killing** for cases where you want to leave the agent running and close your laptop (§11.3).
- **Session binding to client cert fingerprint** so a stolen cookie alone is not enough (§10.2).
- **Workspace isolation guards** (canonicalization, nosymfollow) (§10.9).
- **Connector signing** as an optional v1.1 feature for multi-admin setups (§10.6).

---

## 19. Glossary

- **Run** — a single execution of an agent against a single task.
- **Connector** — a JSON spec describing how to launch a particular agent CLI.
- **Profile** — `confirm` or `yolo`, a permission posture for a connector.
- **Worktree** — a per-run git working directory branched from the project's default branch.
- **Verdict** — Argus's structured judgment of a run: `pass | partial | fail`.
- **Fix-task** — an auto-created task addressing Argus findings; parented to the original task.

---

*Daedalus built the Labyrinth, and his automatons remembered their orders.*
