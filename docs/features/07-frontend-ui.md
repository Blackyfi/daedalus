# 07 — Frontend SPA & Realtime (Iris)

Source: `frontend/src/` (React + TypeScript + Vite + Tailwind + xterm.js + Recharts +
TanStack Query + Zustand). Realtime: `backend/daedalus/iris/`.

## Pages

- **Login** (`LoginPage.tsx`) — 3-factor flow (password → OTP → TOTP/recovery), WebAuthn
  hardware-key shortcut, boot session probe.
- **Project list / dashboard** (`ProjectListPage.tsx`, `ProjectCard.tsx`) — card grid with
  status tone, KPI badges, "+N done since last visit" deltas, 7-day cycle time, global
  Run-all, discover-repos button, active-runner badge.
- **Project page** (`ProjectPage.tsx`) — header metadata, plan-from-ideas, ship button,
  Run-all w/ eligibility count, git-pull banner, rate-limit banner, action-bar "inbox".
- **Connectors** (`ConnectorsPage.tsx`) — table, enable/disable, reload pack, per-connector
  override editor (models, wall-clock, Argus, fix-loops, force-overrides).
- **KPIs** (`KPIPage.tsx`) — Recharts stacked-area task-status time-series; project selector;
  7/14/30/90-day ranges; status totals.
- **Audit** (`AuditPage.tsx`) — event table; filters (all/anomalies/UI/auth/run); anomaly +
  UI counters; payload/stack formatting; cert-fp display.
- **Security** (`SecurityPage.tsx`) — WebAuthn key management (enroll, list, delete, transports,
  last-used).
- **Algorithms** (`AlgorithmsPage.tsx`) — self-documenting Mermaid diagrams of 6 core
  algorithms with source links.

## Key components

- **TaskBoard** — 6-column kanban (backlog→ready→running→verifying→needs_fixes→done),
  mobile swipe picker, new-task form, per-task run button.
- **RunPanel** — xterm.js live PTY terminal, transcript replay, input takeover/release,
  pause/resume/interrupt/kill/detach controls, watchdog diagnostic, token/cost display,
  recent-runs sidebar (paginated), Argus verdict card, rollback + retry buttons.
- **DiffViewer** — split & unified diff, syntax highlighting, line gutters.
- **PlanReview** — editable proposed-task cards, rationale, confirm-all/discard.
- **IdeaBox** — idea CRUD, inline editing, promoted/pending badges, tags.
- **MergeBatchModal** — stepper (preview → merge/resolve → ship), conflict list, verify
  output, batch item states, prune option.
- **DiscoverModal** — filesystem repo scan, bulk select, per-repo name/connector, register.
- **ProjectSettings** — connector, model selectors, Argus toggle, fix-loops, wall-clock
  override, monthly cost cap.
- **Shell** — header nav (Projects/KPIs/Connectors/Audit/Security/Algorithms), mobile drawer,
  RunnerBar (active count), SubscriptionChip (plan + quota), flash banners.
- Supporting: ErrorBoundary, FailureOverlay, GitPullBanner, HelpTooltip, MermaidDiagram,
  ProjectActionBar, SubscriptionChip, RunnerBar.

## Realtime (Iris)

- `wss://…/ws/pty/{run_id}` — live terminal; JSON envelope (`data`/`state`/`input`/
  `takeover`/`release`/`ping`); multi-attach hand-off with holder state in Redis
  (`pty:holder:{rid}`, 120 s TTL).
- `wss://…/ws/projects/{project_id}/events` — project event fan-out.
- `wss://…/ws/queue` — queue depth.
- All session-cookie + cert-fingerprint authenticated.
- SPA polling intervals: tasks 5 s, runs 3 s, plans 5 s, git status 60 s.

## State & UX

- Zustand store (auth, banners), TanStack Query caching, localStorage per-project visit
  snapshots, flash toasts (5 s), error boundaries.
- Responsive 320 px → 1600 px; touch targets; client-side diagnostics
  (`POST /api/v1/diagnostics/log`, 30/min) recorded as `ui.*` audit events.
