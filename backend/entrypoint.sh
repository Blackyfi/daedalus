#!/usr/bin/env bash
# Daedalus container entrypoint. Behaviour depends on $ROLE:
#   api    → run alembic upgrade head, then uvicorn
#   iris   → run uvicorn-backed iris fan-out
#   hermes → run the scheduler
#   talos  → run the agent supervisor
#   argus  → run the argus-mode agent supervisor
set -euo pipefail

ROLE="${ROLE:-api}"

case "$ROLE" in
    api)
        if [[ "${DAEDALUS_AUTO_MIGRATE:-true}" == "true" ]]; then
            echo "[entrypoint] running alembic upgrade head"
            alembic upgrade head
        fi
        exec uvicorn daedalus.main:app --host 0.0.0.0 --port 8000
        ;;
    iris)
        exec python -m daedalus.iris.main
        ;;
    hermes)
        exec python -m daedalus.hermes.worker
        ;;
    talos|argus)
        exec python -m daedalus.talos
        ;;
    *)
        echo "[entrypoint] unknown ROLE=$ROLE" >&2
        exit 64
        ;;
esac
