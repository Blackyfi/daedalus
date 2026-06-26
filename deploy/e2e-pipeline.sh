#!/usr/bin/env bash
# Provision run-pipeline state on the isolated test stack, then run the
# Playwright flows that need it (#9 ship-undo, #10 plan steering).
#
# Prereqs: `make test.up.full` is running (api/hermes/talos as uid 1000, litellm
# up) and the test stack answers at $TEST_URL. Everything here is disposable and
# confined to the daedalus-test project + its isolated workspaces.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_URL="${TEST_URL:-https://localhost:9543}"
PROJ="daedalus-test"
MOCK="$ROOT/deploy/test-workspaces/mockrepo"
CJ="$(mktemp)"
trap 'rm -f "$CJ"' EXIT
c(){ curl -sk -b "$CJ" "$@"; }
jq_(){ python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

echo "==> ensure isolated mock repo"
if [[ ! -d "$MOCK/.git" ]]; then
  mkdir -p "$MOCK"; ( cd "$MOCK"
    git init -q -b main
    git config user.email e2e@daedalus.test; git config user.name "E2E Fixture"
    printf '# Mock Repo\n' > README.md; printf 'line 0\n' > CHANGELOG.md
    git add -A && git commit -q -m "init mock repo" )
fi
echo "==> reset mock repo to a clean main"
docker exec "${PROJ}-api-1" bash -lc '
  cd /workspaces/mockrepo
  rm -rf runs; git worktree prune
  for b in $(git branch --format="%(refname:short)" | grep -v "^main$"); do git branch -D "$b"; done
  git reset -q --hard "$(git rev-list --max-parents=0 HEAD | tail -1)"'

echo "==> login (3FA bypass) + import connectors"
curl -sk -c "$CJ" -X POST "$TEST_URL/api/v1/auth/test-login" -H 'Content-Type: application/json' -d '{}' >/dev/null
c -X POST "$TEST_URL/api/v1/connectors/reload" >/dev/null

echo "==> MERGE project: task -> run -> awaiting_review batch"
MPID=$(c -X POST "$TEST_URL/api/v1/projects" -H 'Content-Type: application/json' -d '{"name":"UI Merge","workspace_path":"/workspaces/mockrepo","git_default_branch":"main","default_connector_id":"shell-repo-demo","argus_enabled":false}' | jq_ "['id']")
MTID=$(c -X POST "$TEST_URL/api/v1/projects/$MPID/tasks" -H 'Content-Type: application/json' -d '{"title":"UI undo task","description":"x","priority":"P1","connector_id":"shell-repo-demo","profile":"confirm"}' | jq_ "['id']")
MRID=$(c -X POST "$TEST_URL/api/v1/tasks/$MTID/run?force=true" -H 'Content-Type: application/json' -d '{}' | jq_ "['id']")
for _ in $(seq 1 40); do st=$(c "$TEST_URL/api/v1/runs/$MRID" | jq_ "['state']"); case "$st" in completed|failed|cancelled|aborted_unsafe) break;; esac; sleep 3; done
[[ "$st" == completed ]] || { echo "merge run did not complete (state=$st)"; exit 1; }
MERGE_PID=$MPID
c -X POST "$TEST_URL/api/v1/projects/$MPID/merge-batch" -H 'Content-Type: application/json' -d "{\"task_ids\":[\"$MTID\"],\"require_argus_pass\":false}" >/dev/null

echo "==> PLAN project: ideas -> pending plan"
PLAN=$(c -X POST "$TEST_URL/api/v1/projects" -H 'Content-Type: application/json' -d '{"name":"UI Plan","workspace_path":"/workspaces/mockrepo","git_default_branch":"main","default_connector_id":"shell-repo-demo","argus_enabled":false}' | jq_ "['id']")
for t in "Write API docs" "Add rate-limit tests"; do
  c -X POST "$TEST_URL/api/v1/projects/$PLAN/ideas" -H 'Content-Type: application/json' -d "{\"text\":\"$t\",\"tags\":[\"x\"]}" >/dev/null
done
c -X POST "$TEST_URL/api/v1/projects/$PLAN/plan" -H 'Content-Type: application/json' -d '{}' >/dev/null
for _ in $(seq 1 20); do n=$(c "$TEST_URL/api/v1/projects/$PLAN/plans?status=pending" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))"); [[ "$n" != 0 ]] && break; sleep 3; done
[[ "${n:-0}" != 0 ]] || { echo "no plan proposal produced"; exit 1; }

echo "==> MERGE_PID=$MERGE_PID  PLAN_PID=$PLAN"
echo "==> running Playwright pipeline flows"
cd "$ROOT/frontend"
MERGE_PID="$MERGE_PID" PLAN_PID="$PLAN" E2E_BASE_URL="$TEST_URL" npm run test:e2e -- e2e/pipeline.spec.ts
