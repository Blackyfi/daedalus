#!/usr/bin/env bash
# Daedalus pg-backup — single-shot backup invocation.
#
# Runs `pg_dump` against the configured Postgres, gzips the output, uploads
# it to MinIO (via mc), and prunes anything in the bucket older than
# $PG_BACKUP_RETENTION_DAYS days.
#
# All knobs come from environment variables — see deploy/docker-compose.yml
# (pg-backup service) and .env.example.

set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${S3_ENDPOINT:?S3_ENDPOINT is required (e.g. http://minio:9000)}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
: "${PG_BACKUP_BUCKET:=daedalus-backups}"
: "${PG_BACKUP_RETENTION_DAYS:=30}"

log() { printf '%s pg-backup %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }

stamp="$(date -u +'%Y%m%dT%H%M%SZ')"
dumpfile="/tmp/daedalus-${stamp}.sql.gz"
key="${POSTGRES_DB}/${stamp}.sql.gz"

cleanup() { rm -f "$dumpfile" 2>/dev/null || true; }
trap cleanup EXIT

# 1. Configure mc (idempotent — alias overwrites silently).
mc alias set daedalus "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null

# 2. Make sure the bucket exists.
if ! mc ls "daedalus/${PG_BACKUP_BUCKET}" >/dev/null 2>&1; then
  log "creating bucket ${PG_BACKUP_BUCKET}"
  mc mb --ignore-existing "daedalus/${PG_BACKUP_BUCKET}"
fi

# 3. Run pg_dump.
log "dumping ${POSTGRES_DB} from ${POSTGRES_HOST}:${POSTGRES_PORT}"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    --host="$POSTGRES_HOST" \
    --port="$POSTGRES_PORT" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --no-owner \
    --no-privileges \
    --format=plain \
    --clean --if-exists \
  | gzip -9 > "$dumpfile"

size="$(stat -c '%s' "$dumpfile" 2>/dev/null || stat -f '%z' "$dumpfile")"
log "dump complete (${size} bytes) — uploading to ${PG_BACKUP_BUCKET}/${key}"

# 4. Upload.
mc cp "$dumpfile" "daedalus/${PG_BACKUP_BUCKET}/${key}"

# 5. Prune anything older than retention.
if [ "$PG_BACKUP_RETENTION_DAYS" -gt 0 ]; then
  log "pruning dumps older than ${PG_BACKUP_RETENTION_DAYS} days"
  # `mc rm --older-than` accepts Nd
  mc rm \
      --recursive --force \
      --older-than "${PG_BACKUP_RETENTION_DAYS}d" \
      "daedalus/${PG_BACKUP_BUCKET}/${POSTGRES_DB}/" \
    || log "prune step had warnings; continuing"
fi

log "backup OK"
