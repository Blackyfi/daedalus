# Daedalus Postgres backup sidecar

Runs nightly `pg_dump` against the `postgres` service and uploads the
gzipped result to MinIO. Old dumps are pruned by the same job using
`mc rm --older-than`.

## Configuration

All knobs are environment variables, set in `.env`:

| variable | default | meaning |
|---|---|---|
| `PG_BACKUP_SCHEDULE` | `30 2 * * *` | 5-field cron expression (UTC). |
| `PG_BACKUP_RETENTION_DAYS` | `30` | Older dumps in `${PG_BACKUP_BUCKET}/${POSTGRES_DB}/` are deleted. `0` disables pruning. |
| `PG_BACKUP_BUCKET` | `daedalus-backups` | Created on first run if absent. |

The sidecar reuses the platform's `S3_ENDPOINT` / `S3_ACCESS_KEY` /
`S3_SECRET_KEY` so no extra credentials live in compose.

## Operations

```bash
# Force a backup now (does not wait for the cron firing)
make backup.now

# List what's in the bucket
make backup.list

# Restore — pick a key from `backup.list` and pass it
make backup.restore KEY=daedalus/20260503T0230Z.sql.gz
```

The restore target streams the dump straight into `psql` against the
running database. If you need an offline restore (e.g. into a fresh
database), copy the dump out of the container and run `psql --create`
manually.

## Layout in the bucket

```
${PG_BACKUP_BUCKET}/
└── ${POSTGRES_DB}/
    ├── 20260501T0230Z.sql.gz
    ├── 20260502T0230Z.sql.gz
    └── 20260503T0230Z.sql.gz
```

One dump per cron firing. The retention prune scans this prefix.

## Notes

- The sidecar runs an initial backup on boot, then sleeps until the next
  cron-style firing of `PG_BACKUP_SCHEDULE`. There is no real cron
  daemon — the loop is in `entrypoint.sh`.
- `pg_dump` runs with `--clean --if-exists --no-owner --no-privileges`,
  matching the Alembic-managed schema layout.
- The sidecar shares `backnet` with `postgres` and `minio` and never
  needs internet access.
