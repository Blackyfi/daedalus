# 03 — Tasks, Ideas, Notes & LLM Planning

Source: `backend/daedalus/api/routes/tasks.py`, `ideas.py`, `notes.py`, `plans.py`,
`internal.py`, `planning/planner.py`, `db/status_history.py`.

## Tasks

- CRUD: `GET/POST /api/v1/projects/{pid}/tasks`, `PATCH/DELETE /api/v1/tasks/{tid}`.
- Fields: `title` (1–240), `description`, `acceptance_criteria`, `priority` (P0–P3),
  `status` (backlog/ready/in_progress/verifying/needs_fixes/done/cancelled),
  `connector_id`, `profile` (default `confirm`), `depends_on` (DAG), `tags`,
  `estimated_minutes`, `fix_loop_count`, `parent_task_id` (hierarchy).
- `POST /api/v1/tasks/{tid}/run` — enqueue single task; guards: git-pull-required,
  monthly cost cap; flips task to `ready` atomically (`tasks.py:279-322`).
- `POST /api/v1/projects/{pid}/run-all` — bulk-enqueue eligible tasks (backlog/ready/
  needs_fixes), excludes active runs, returns enqueued + skip reasons (`tasks.py:187-276`).

## Task status time-series

- Every status transition captured to `task_status_events` via SQLAlchemy before_flush
  listener (`status_history.py:23-77`) — powers KPI charts.

## Ideas (brainstorm box)

- CRUD: `GET/POST /api/v1/projects/{pid}/ideas`, `PATCH/DELETE /api/v1/ideas/{iid}`.
- Supports `text`/`body` alias; locked after archival; `sort_index` ordering.
- `POST /api/v1/projects/{pid}/plan` — trigger an LLM planning run from ideas.

## Notes

- CRUD: `GET/POST /api/v1/projects/{pid}/notes`, `PATCH/DELETE /api/v1/notes/{nid}`,
  ordered by `updated_at` desc.

## LLM planning & plan review

- **Planning run** reads project context (repo tree ≤200 lines/4 levels, README ≤4 k chars,
  recent 40 tasks, connectors, ideas) and returns a dependency-ordered proposal with
  rationale (`planner.py:95-294`).
- Deterministic 1-idea→1-task fallback when LLM unreachable (`planner.py:193-241`).
- `POST /api/internal/planning/generate` — internal ingestion (HMAC `X-Daedalus-Internal-Key`),
  honors connector model overrides (`internal.py:28-122`).

### Plan-review lifecycle

- `GET /api/v1/projects/{pid}/plans?status=pending|confirmed|discarded`.
- `GET /api/v1/plans/{plan_id}`.
- `POST /api/v1/plans/{plan_id}/confirm` — optional edits to tasks/rationale, resolves
  dependency indices → UUIDs, optionally archives source ideas (`plans.py:74-147`).
- `POST /api/v1/plans/{plan_id}/discard`.
- Proposal fields: `proposed_tasks` (title/description/acceptance_criteria/priority/
  depends_on/suggested_connector/tags/source_idea_id), `rationale`, `source_idea_ids`,
  `status`, `confirmed_at`, `run_id`.
