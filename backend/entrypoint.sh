#!/usr/bin/env bash
# Daedalus container entrypoint. Behaviour depends on $ROLE:
#   api    → run alembic upgrade head, then uvicorn
#   iris   → run uvicorn-backed iris fan-out
#   hermes → run the scheduler
#   talos  → run the agent supervisor
#   argus  → run the argus-mode agent supervisor
set -euo pipefail

ROLE="${ROLE:-api}"

# If the operator's SSH config is bind-mounted at /mnt/host-ssh, copy it
# into the runtime user's $HOME/.ssh and rewrite ownership. OpenSSH
# refuses to use config/key files whose owner is neither root nor the
# current uid; the bind-mount preserves host ownership (typically uid
# 1000) which doesn't match root. A copy-then-chown sidesteps the rule
# without forcing the api/hermes containers off root. Idempotent.
if [[ -d /mnt/host-ssh ]]; then
    target_home="${HOME:-/root}"
    mkdir -p "${target_home}/.ssh"
    chmod 700 "${target_home}/.ssh"
    cp -rL /mnt/host-ssh/. "${target_home}/.ssh/" 2>/dev/null || true
    chown -R "$(id -u):$(id -g)" "${target_home}/.ssh"
    chmod 600 "${target_home}/.ssh"/* 2>/dev/null || true
    find "${target_home}/.ssh" -name '*.pub' -exec chmod 644 {} + 2>/dev/null || true
fi

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
