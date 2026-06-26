# 02 — Projects, Stats, Git Status & Discovery

Source: `backend/daedalus/api/routes/projects.py`, `discovery.py`, `git_status.py`,
`costs.py`, `db/models.py`.

## Project CRUD

- `GET /api/v1/projects` — list (owner sees all; members see own).
- `POST /api/v1/projects` — create with workspace-path canonicalization.
- `GET /api/v1/projects/{pid}` — single project incl. rate-limit pause state.
- `PATCH /api/v1/projects/{pid}` — update settings.
- `DELETE /api/v1/projects/{pid}` — archive/soft-delete.

## Project settings

- `default_connector_id`, `git_default_branch` (default `main`).
- `max_fix_loops` (0–20, default 3), `auto_run_fix`.
- Per-project LLM model overrides: `planning_model`, `task_model`, `verifier_model`.
- `argus_enabled` — verification toggle.
- `wall_clock_minutes_override` (1–1440, nullable).
- `monthly_cost_cap_usd_micros` — blocks new runs at cap (402).
- `archived` — soft-delete flag.

## Stats & KPIs

- `GET /api/v1/projects/stats` — per project: task counts by status, total,
  `last_activity_at`, `avg_cycle_seconds_7d`, `completed_in_window_7d`,
  `cost_cap_usd_micros`, `month_cost_usd_micros` (`projects.py:105-219`).

## Git-status integration

- `GET /api/v1/projects/git-status` — cached bulk status for all visible projects.
- `GET /api/v1/projects/{pid}/git-status?refresh=true` — per project, optional fresh fetch.
- Fields: `is_git_repo`, `branch`, `upstream`, `ahead_count`, `behind_count`, `has_remote`,
  `fetch_failed`, `fetch_error`, `last_fetched_at`, `needs_pull()` (`git_status.py`).
- Redis-cached (60 s TTL); fetch timeout 15 s, rev-list 10 s.
- **Enqueue guard**: blocks task runs when workspace is behind upstream (unless `force=true`).

## Cost tracking & monthly caps

- `monthly_cost_cap_usd_micros` (nullable) enforced at enqueue: `402 cost_cap_reached`
  when calendar-month spend (`SUM(Run.cost_usd_micros)`) reaches cap (`costs.py`).
- Month boundary = UTC calendar month.

## Repository discovery

- `GET /api/v1/discover/repos` — walks workspaces root (3 levels deep), skips
  `.git/node_modules/.venv/...`; returns name, path, relative_path, default_branch,
  README-derived description, last_commit_at, has_uncommitted, already_registered
  (`discovery.py:1-210`).
- `POST /api/v1/discover/register` — bulk-create Project rows for selected repos
  (validates paths inside workspaces root, skips already-registered).

## Authorization model

- Roles: `owner` / `member` / `viewer`.
- Owner-only: audit log, connector admin, discovery.
- Members: own projects only.
