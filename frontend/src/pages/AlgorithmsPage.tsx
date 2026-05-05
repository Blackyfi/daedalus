import { useState } from "react";
import MermaidDiagram from "../components/MermaidDiagram";

interface Algo {
  id: string;
  title: string;
  blurb: string;
  refs: string[];
  diagrams: { id: string; caption: string; chart: string }[];
  notes?: string[];
}

// ── Run all ────────────────────────────────────────────────────────────────

const RUN_ALL: Algo = {
  id: "run-all",
  title: "Run all",
  blurb:
    "Bulk-enqueue every backlog / ready / needs-fixes task on the project. " +
    "Hermes serializes runs per-project and DAG-resolves dependents; up to " +
    "MAX_CONCURRENT_PROJECTS projects run in parallel.",
  refs: [
    "backend/daedalus/api/routes/tasks.py: run_all_tasks",
    "backend/daedalus/hermes/client.py: HermesClient.enqueue_task",
    "backend/daedalus/hermes/scheduler.py: HermesScheduler._worker_loop",
    "backend/daedalus/talos/runner.py: TalosRunner._execute_task",
  ],
  diagrams: [
    {
      id: "run-all-flow",
      caption: "End-to-end flow: from the Run-all button to a task being verified",
      chart: `
flowchart TD
  click([User clicks &quot;Run all&quot; on the Project page]) --> confirm{Confirm dialog<br/>shows N tasks}
  confirm -->|cancel| stop1([noop])
  confirm -->|ok| post[POST /api/v1/projects/:pid/run-all]

  post --> auth{Auth + project access?}
  auth -->|fail| err403([403])
  auth -->|ok| git{Workspace behind upstream?}
  git -->|yes & !force| err409a([409 git_pull_required])
  git -->|no or ?force=true| select[SELECT tasks WHERE status IN<br/>backlog · ready · needs_fixes<br/>ORDER BY priority asc, created_at asc]

  select --> any{Any eligible?}
  any -->|no| err409b([409 no eligible tasks])
  any -->|yes| forEach[For each task]

  forEach --> enqueue[HermesClient.enqueue_task<br/>· INSERT runs row state=queued<br/>· Record DAG deps in Redis<br/>· git worktree add for the run<br/>· yolo? snapshot pre-run<br/>· RPUSH hermes:queue:default]
  enqueue --> nextTask{More tasks?}
  nextTask -->|yes| forEach
  nextTask -->|no| audit[Audit log: project.run_all]
  audit --> return([Return list of Run rows])

  return -.->|asynchronously| schedHeader[Hermes scheduler<br/>N worker coroutines]
  schedHeader --> scan[LRANGE queue:urgent → :default → :bg<br/>find first entry with idle project + deps met]
  scan --> lua[Atomic Lua script:<br/>SCARD active_projects &lt; cap?<br/>EXISTS project_lease?<br/>LREM queue payload<br/>SET project_lease<br/>SADD active_projects]
  lua -->|nil| scan
  lua -->|payload| claimed[State: queued → claimed → running<br/>started_at = now]
  claimed --> talos[PUBLISH hermes:signal:&lt;run_id&gt;<br/>action = run]
  talos --> pty[Talos spawns the connector CLI<br/>inside a real PTY<br/>streams pty:&lt;run_id&gt; to Redis]
  pty --> done{Agent emits done signal?<br/>regex / exit_code / tool_call}
  done -->|wall_clock or idle| killed[Talos kills process<br/>state = failed]
  done -->|yes| transcript[Persist transcript to MinIO<br/>parse usage tokens]
  transcript --> complete[SET hermes:completion:&lt;run_id&gt;]
  complete --> finalise[Hermes worker reads completion<br/>UPDATE runs row<br/>release project lease]

  finalise --> verify{Connector has verify_commands?}
  verify -->|no| markDone([task → done])
  verify -->|yes| argus[Enqueue Argus run<br/>same project lease taken sequentially]
  argus --> argusVerdict{Argus verdict}
  argusVerdict -->|pass| markDone
  argusVerdict -->|partial / fail| needsFix[task → needs_fixes<br/>diff hash recorded]
  needsFix --> autoFix{auto_run_fix?<br/>diff stable? loop &lt; max?}
  autoFix -->|no| stopFix([wait for human])
  autoFix -->|yes| spawnFix[Insert Fix task at urgent priority<br/>HermesClient.enqueue_task]
  spawnFix --> forEach
`.trim(),
    },
    {
      id: "run-all-states",
      caption: "Task status transitions Run-all walks each task through",
      chart: `
stateDiagram-v2
  [*] --> backlog
  backlog --> ready: included by Run-all
  ready --> in_progress: Hermes claims
  in_progress --> verifying: agent done · verify_commands present
  in_progress --> done: agent done · no verify_commands
  in_progress --> needs_fixes: agent failed
  verifying --> done: Argus verdict = pass
  verifying --> needs_fixes: Argus verdict = partial / fail
  needs_fixes --> ready: Run-all picks it up again
  needs_fixes --> ready: auto_run_fix re-queues it
  done --> [*]
  cancelled --> [*]
`.trim(),
    },
  ],
  notes: [
    "Per-task ?force=true bypasses the git-pull guard if you really need to run on a stale tree.",
    "needs_fixes is included in Run-all (matches the per-task ▶ Run button); auto_run_fix only controls automatic re-queue after Argus, not what Run-all picks up.",
    "DAG dependencies (task.depends_on) are honoured: Hermes skips a queued run until every dep is in a terminal state.",
    "Each agent run writes into a fresh git worktree; Argus reads the diff vs default branch from there.",
  ],
};

// ── Per-project lease (atomic claim) ───────────────────────────────────────

const PROJECT_LEASE: Algo = {
  id: "project-lease",
  title: "Per-project lease — atomic claim",
  blurb:
    "Guarantees one Daedalus-managed run per project at a time, while " +
    "letting up to MAX_CONCURRENT_PROJECTS projects run in parallel. The " +
    "claim is one Lua script so cap-check, lease-check, queue dequeue, and " +
    "lease set are a single Redis transaction.",
  refs: [
    "backend/daedalus/hermes/leases.py: try_claim, _CLAIM_LUA",
    "backend/daedalus/hermes/scheduler.py: _try_claim_idle_project_job",
    "project-plan.md §6.3.1, §6.3.2",
  ],
  diagrams: [
    {
      id: "lease-claim",
      caption: "Atomic claim path executed inside Redis",
      chart: `
flowchart TD
  worker[Worker coroutine] --> snapshot[Snapshot active_projects set]
  snapshot --> capCheck{SCARD active_projects<br/>&lt; cap?}
  capCheck -->|no| idle([Sleep poll_interval])
  capCheck -->|yes| scanLane[LRANGE hermes:queue:urgent · default · bg]
  scanLane --> nextEntry[Take next entry whose project ∉ active_projects<br/>and DAG deps met]
  nextEntry -->|none| idle

  nextEntry --> lua[Run Lua script atomically]
  lua --> luaCap{SCARD active_projects<br/>&lt; cap?}
  luaCap -->|no| nilA([return nil])
  luaCap -->|yes| luaLease{EXISTS project_lease?}
  luaLease -->|yes| nilB([return nil])
  luaLease -->|no| luaLrem[LREM queue 1 payload]
  luaLrem -->|0| nilC([return nil])
  luaLrem -->|1| luaSet[SET project_lease run_id EX ttl<br/>SADD active_projects project_id]
  luaSet --> ok([return payload])

  nilA --> scanLane
  nilB --> scanLane
  nilC --> scanLane
  ok --> dispatch[Dispatch to Talos<br/>heartbeat lease every 60s]
  dispatch --> release[On completion:<br/>DEL project_lease<br/>SREM active_projects]
`.trim(),
    },
  ],
  notes: [
    "Heartbeat refreshes lease TTL every 60s during a run; if the lease ever vanishes mid-run, the dispatcher publishes a kill signal so two workers can never own the same project.",
    "Orphan reclaim on Hermes startup: any runs row in running/claimed without a lock key gets transitioned to aborted_unsafe.",
    "TTL = wall_clock_minutes + PROJECT_LEASE_GRACE_SECONDS (default 5 min).",
  ],
};

// ── Pythia ─────────────────────────────────────────────────────────────────

const PYTHIA: Algo = {
  id: "pythia",
  title: "Pythia — subscription oracle",
  blurb:
    "Talos refreshes a SubscriptionInfo snapshot every PYTHIA_REFRESH_SECONDS " +
    "by calling the same Anthropic OAuth endpoints `claude` itself uses. " +
    "API serves the cached snapshot — never blocks on the network.",
  refs: [
    "backend/daedalus/pythia/probe.py",
    "backend/daedalus/talos/runner.py: _start_pythia_thread",
    "backend/daedalus/api/routes/system.py: get_subscription",
  ],
  diagrams: [
    {
      id: "pythia-flow",
      caption: "Probe + cache, served via /api/v1/system/subscription",
      chart: `
flowchart LR
  subgraph Talos
    timer[Periodic thread<br/>every PYTHIA_REFRESH_SECONDS]
    timer --> creds[Read ~/.claude/.credentials.json]
    creds --> profile[GET /api/oauth/profile<br/>via Anthropic]
    profile --> usage[GET /api/oauth/usage<br/>via Anthropic]
    usage --> merge[Merge into SubscriptionInfo]
    merge --> cache[(Redis<br/>daedalus:subscription:claude<br/>EX cache_ttl)]
  end
  subgraph API
    chip[GET /api/v1/system/subscription] --> read[(Redis read)]
    read --> serve[200 JSON]
  end
  cache -.-> read
`.trim(),
    },
    {
      id: "pythia-kinds",
      caption: "SubscriptionInfo.kind decision tree",
      chart: `
flowchart TD
  start([Probe starts]) --> hasCreds{Credentials file?}
  hasCreds -->|no| missing([cli_missing])
  hasCreds -->|yes| call[GET /api/oauth/profile]
  call --> resp{HTTP status}
  resp -->|timeout| timeout([timeout])
  resp -->|401| auth([auth_required])
  resp -->|200| usage[GET /api/oauth/usage]
  resp -->|other| err([error])
  usage --> hasPlan{plan + usage parsed?}
  hasPlan -->|both| ok([ok])
  hasPlan -->|profile only| ok2([ok with degraded usage])
  hasPlan -->|nothing| unparsed([unparsed])
`.trim(),
    },
  ],
};

// ── Live runner transcript replay ──────────────────────────────────────────

const LIVE_RUNNER: Algo = {
  id: "live-runner",
  title: "Live runner — transcript replay + watchdog",
  blurb:
    "When the user opens a finished run, the xterm pre-fills with the " +
    "persisted transcript so the terminal isn't empty. A 10-second watchdog " +
    "fires a `live_runner_empty` diagnostic if a non-queued run renders zero " +
    "bytes — surfacing silent failures in the audit log.",
  refs: [
    "frontend/src/components/RunPanel.tsx",
    "frontend/src/diagnostics.ts",
    "backend/daedalus/api/routes/diagnostics.py",
    "backend/daedalus/iris/main.py: pty_stream",
  ],
  diagrams: [
    {
      id: "runner-attach",
      caption: "RunPanel attach sequence",
      chart: `
sequenceDiagram
  participant U as User
  participant SPA as RunPanel
  participant API as Daedalus API
  participant S3 as MinIO/S3
  participant Iris as Iris WS
  participant R as Redis

  U->>SPA: clicks a run
  SPA->>SPA: termRef.clear(), bytesReceivedRef = 0

  alt run is in terminal state
    SPA->>API: GET /runs/:rid/transcript/text
    API->>S3: download transcript_object_key
    S3-->>API: bytes
    API-->>SPA: text
    SPA->>SPA: term.write(text), bytesReceived += len
  end

  SPA->>Iris: WS /ws/pty/:rid
  Iris->>R: XREAD pty:&lt;rid&gt; from id=0
  R-->>Iris: retained PTY frames
  loop per frame
    Iris-->>SPA: {t:"data", d}
    SPA->>SPA: term.write(d), bytesReceived += len
  end

  SPA->>SPA: setTimeout 10s watchdog
  alt bytesReceived === 0 after 10s
    SPA->>API: POST /diagnostics/log {kind:"live_runner_empty"}
    API->>API: append AuditEvent action=ui.live_runner_empty
  end
`.trim(),
    },
  ],
  notes: [
    "The watchdog also reports pty_ws_error / pty_ws_closed_early / transcript_fetch_failed diagnostics, all visible in Audit → UI diagnostics.",
    "Per-tab dedupe + 60s suppression so a sticky bug doesn't flood the audit log.",
  ],
};

// ── Git pull guard ─────────────────────────────────────────────────────────

const GIT_GUARD: Algo = {
  id: "git-guard",
  title: "Git pull guard",
  blurb:
    "Before launching agents, Daedalus checks the project workspace against " +
    "its upstream. If behind, the red banner appears and run buttons are " +
    "disabled until the user pulls (or passes ?force=true).",
  refs: [
    "backend/daedalus/git_status.py",
    "backend/daedalus/api/routes/projects.py: project_git_status, bulk_git_status",
    "backend/daedalus/api/routes/tasks.py: _ensure_not_behind",
    "frontend/src/components/GitPullBanner.tsx",
  ],
  diagrams: [
    {
      id: "git-guard-flow",
      caption: "Probe + cache + enqueue gate",
      chart: `
flowchart TD
  open([User opens project page]) --> banner[GitPullBanner mounts]
  banner --> getStatus[GET /projects/:id/git-status?refresh=true]

  getStatus --> probe[git_status.get_status]
  probe --> cache{Redis cache hit?<br/>(skipped on refresh)}
  cache -->|hit| serve[Return cached]
  cache -->|miss / refresh| isRepo{git rev-parse --git-dir}
  isRepo -->|no| notRepo([is_git_repo=false])
  isRepo -->|yes| upstream[git rev-parse --abbrev-ref @{u}]
  upstream -->|no upstream| local([local-only repo])
  upstream -->|ok| fetch[git fetch with timeout]
  fetch -->|fail| markFail[fetch_failed=true · keep last counts]
  fetch -->|ok| revList[git rev-list --left-right --count @{u}...HEAD]
  revList --> counts[behind_count, ahead_count]
  counts --> writeCache[(Redis cache EX 60s)]
  markFail --> writeCache
  writeCache --> serve

  serve --> show{behind_count &gt; 0?}
  show -->|no| hide([banner hidden])
  show -->|yes| red[Red banner shown<br/>run buttons disabled]

  red -.-> enqueueAttempt[User clicks Run / Run all / Retry]
  enqueueAttempt --> guard{_ensure_not_behind}
  guard -->|behind & !force| reject([409 git_pull_required])
  guard -->|fresh| proceed([enqueue normally])
`.trim(),
    },
  ],
};

const ALGORITHMS: Algo[] = [RUN_ALL, PROJECT_LEASE, PYTHIA, LIVE_RUNNER, GIT_GUARD];

// ── Page ───────────────────────────────────────────────────────────────────

export default function AlgorithmsPage() {
  const [activeId, setActiveId] = useState<string>(ALGORITHMS[0].id);
  const active = ALGORITHMS.find((a) => a.id === activeId) ?? ALGORITHMS[0];

  return (
    <div className="grid grid-cols-12 gap-6">
      <aside className="col-span-3 panel sticky top-4 self-start">
        <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">
          Algorithms
        </h2>
        <nav className="flex flex-col gap-1 text-sm">
          {ALGORITHMS.map((a) => (
            <button
              key={a.id}
              onClick={() => setActiveId(a.id)}
              className={`rounded px-2 py-1 text-left transition-colors ${
                a.id === activeId
                  ? "bg-accent/10 text-accent"
                  : "text-muted hover:text-text"
              }`}
            >
              {a.title}
            </button>
          ))}
        </nav>
        <p className="mt-4 text-[11px] text-muted">
          Diagrams are rendered with Mermaid from text — sources live in
          <code className="ml-1">frontend/src/pages/AlgorithmsPage.tsx</code>.
        </p>
      </aside>

      <section className="col-span-9 space-y-6">
        <header className="panel">
          <h1 className="text-xl font-semibold">{active.title}</h1>
          <p className="mt-2 text-sm text-muted">{active.blurb}</p>
          {active.refs.length > 0 && (
            <ul className="mt-3 space-y-0.5 text-[11px] text-muted">
              {active.refs.map((r) => (
                <li key={r}>
                  <code>{r}</code>
                </li>
              ))}
            </ul>
          )}
        </header>

        {active.diagrams.map((d) => (
          <article key={d.id} className="panel">
            <h3 className="mb-2 text-xs uppercase tracking-wide text-muted">
              {d.caption}
            </h3>
            <MermaidDiagram id={`${active.id}-${d.id}`} chart={d.chart} />
          </article>
        ))}

        {active.notes && active.notes.length > 0 && (
          <article className="panel">
            <h3 className="mb-2 text-xs uppercase tracking-wide text-muted">
              Notes
            </h3>
            <ul className="space-y-1.5 text-sm text-text">
              {active.notes.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-accent">·</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          </article>
        )}
      </section>
    </div>
  );
}
