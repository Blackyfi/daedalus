# 05 — Runs, Snapshots/Rollback & Merge/Ship Engine

Source: `backend/daedalus/api/routes/runs.py`, `merges.py`, `merge/`, `storage/objects.py`.

## Runs

- `GET /api/v1/runs/{rid}`, `GET /api/v1/runs/projects/{pid}?limit=1-200`.
- Control: `pause`, `resume`, `interrupt`, `kill`, `detach` (`runs.py:83-87`).
- Interaction: `POST .../inject` (stdin, length audited), `POST .../resize` (rows/cols).
- Output: `GET .../transcript` (object key), `.../transcript/text` (raw),
  `.../diff` (unified vs default branch, cached or on-demand).
- Run fields: `kind`, `state` (queued/claimed/running/completed/failed/cancelled/
  aborted_unsafe), `lane`, timestamps, `exit_code`, `token_input/output`,
  `cost_usd_micros`, `retry_of`, `was_rate_limited`, `retry_after`,
  `transcript_object_key`, `diff_object_key`, `worktree_path`, `connector_snapshot`.

## Snapshots & rollback

- `GET /api/v1/runs/{rid}/snapshot` — pre-yolo snapshot + `git_tag` (200 with null if none).
- **Auto-snapshot**: yolo-profile runs tag `daedalus-snap/<run_id>` at HEAD before executing
  (`client.py:400-437`).
- `POST /api/v1/runs/{rid}/rollback` — `git reset --hard <tag>` + clean; refused on active
  runs (`runs.py:217-272`).
- `POST /api/v1/runs/{rid}/retry` — clone failed/cancelled/aborted run as fresh queued run;
  sets `retry_of` chain pointer; respects git-pull gate (`runs.py:278-370`).
- `GET /api/v1/runs/{rid}/argus` — verification report.

## Merge & ship engine

- `POST /api/v1/projects/{pid}/merge-batch/preview` — categorize candidates without
  persisting: clean / conflict / empty / already_merged / missing_branch / missing_run
  (via `git merge-tree --write-tree` dry-run) (`merges.py:232-260`, `merge/planner.py`).
- `POST /api/v1/projects/{pid}/merge-batch` — create + execute: integration worktree
  (`daedalus-merge-<bid>`), sequential `git merge --no-ff`, run verify commands
  (`merges.py:263-317`, `merge/executor.py`).
- `GET .../merge-batches`, `GET .../merge-batches/{bid}` (state reconciliation).
- `POST .../merge-batches/{bid}/resolve` — **agent-driven conflict resolution**: queue
  yolo resolver run per conflict; auto-merge when earlier merges clear a conflict; reconcile
  vs Argus verdict (`merges.py:364-406`, `merge/resolution.py`).
- `POST .../merge-batches/{bid}/ship` — fast-forward default branch (ancestor-checked),
  optionally prune source branches + remove worktrees (`merges.py:409-446`, `merge/ship.py`).
- `require_argus_pass` filter: include only tasks with verdict=pass.
- States: pending → merging_clean → awaiting_review → resolving → shipping → shipped /
  failed / aborted.

## Object storage (Mnemosyne)

- S3-compatible (boto3, MinIO/AWS) with local filesystem fallback (`storage/objects.py`).
- Transcript key `runs/{run_id}/transcript.log`; empty runs synthesize a minimal record.
