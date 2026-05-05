#!/usr/bin/env bash
# Daedalus pg-backup sidecar entrypoint.
#
# - Runs the backup once at startup so a freshly started stack always has a
#   recent dump to recover from.
# - Sleeps until the next cron-style firing of $PG_BACKUP_SCHEDULE and runs
#   the backup again. Repeats forever.
#
# We avoid pulling in a real cron daemon; the loop is a few lines and keeps
# logs flowing through `docker compose logs`.

set -euo pipefail

: "${PG_BACKUP_SCHEDULE:=30 2 * * *}"

log() { printf '%s pg-backup %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# Compute "seconds until next match of the cron expression" without a real
# cron daemon. We tolerate only the standard 5-field syntax; the env var is
# allowed to use `*`, lists, and ranges via `cron-next` below.
seconds_until_next() {
  local schedule="$1"
  python3 - "$schedule" <<'PY'
import sys, time
from datetime import datetime, timezone

# Tiny cron parser: supports "*" / "*/N" / "A,B,C" / "A-B" per field.
fields = sys.argv[1].split()
if len(fields) != 5:
    print(60); sys.exit(0)

def expand(field, lo, hi):
    if field == "*":
        return set(range(lo, hi + 1))
    out = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            part = base
        if part == "*":
            r = range(lo, hi + 1, step)
        elif "-" in part:
            a, b = part.split("-", 1)
            r = range(int(a), int(b) + 1, step)
        else:
            r = [int(part)]
        out.update(r)
    return out

minutes = expand(fields[0], 0, 59)
hours   = expand(fields[1], 0, 23)
doms    = expand(fields[2], 1, 31)
months  = expand(fields[3], 1, 12)
dows    = expand(fields[4], 0, 6)  # 0=Sun

now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
for offset in range(1, 60 * 24 * 366):  # search up to 1 year ahead
    cand = now.fromtimestamp(now.timestamp() + offset * 60, tz=timezone.utc)
    if (cand.minute in minutes and cand.hour in hours
        and cand.day in doms and cand.month in months
        and cand.weekday() % 7 in dows):
        delta = (cand - datetime.now(timezone.utc)).total_seconds()
        print(int(max(delta, 1)))
        sys.exit(0)
print(86400)
PY
}

# Run once at startup so we always have a fresh dump within ~30 s of boot.
log "boot — running initial backup"
if ! /usr/local/bin/pg-backup; then
  log "initial backup failed — will retry on next schedule"
fi

while true; do
  delay="$(seconds_until_next "$PG_BACKUP_SCHEDULE")"
  next_at="$(date -u -d "@$(($(date +%s) + delay))" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo "+${delay}s")"
  log "next backup at ${next_at} (in ${delay}s, schedule='${PG_BACKUP_SCHEDULE}')"
  sleep "$delay"

  if ! /usr/local/bin/pg-backup; then
    log "scheduled backup failed — continuing"
  fi
done
