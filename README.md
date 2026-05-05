# Daedalus

**Distributed Agent Execution, Direction & Autonomous Lifecycle Unified System**

Self-hosted web platform for orchestrating local AI coding agents (Claude Code, Codex, Qwen, custom shells) against project-scoped task graphs. Single-runner queue, live terminal mirroring, autonomous progress verification, idea-to-task review, pre-yolo workspace snapshots, and 3-factor + mTLS auth.

See [`project-plan.md`](./project-plan.md) for the full design.

## Layout

```
backend/        FastAPI service (Daedalus API + Cerberus auth + Hermes scheduler + Talos PTY runner + Argus LLM verifier + Iris websocket fan-out)
frontend/       React + TypeScript + Vite + Tailwind + xterm.js SPA
connectors/     Default agent connector JSON specs
deploy/         docker-compose, Caddyfile, observability stack, secrets, workspaces mount
```

## Quickstart (development)

> Requires Docker 24+, Docker Compose v2, and Node 22+ (only for `make frontend.dev`).

```bash
cp .env.example .env
# Drop your internal CA bundle + a server cert/key into deploy/secrets/:
#   deploy/secrets/internal_ca.crt
#   deploy/secrets/server.crt
#   deploy/secrets/server.key
make build              # builds api, iris, hermes, talos, argus-worker, frontend
make up                 # api auto-runs `alembic upgrade head` via entrypoint.sh
make init               # interactive: owner account + TOTP enrollment + recovery codes
make seed-connectors    # imports the default connector pack
make obs.up             # optional: prometheus + grafana + loki + otel + mailpit
```

Then browse to `https://daedalus.your.lan:9443` (or whatever you set
`DAEDALUS_HTTPS_PORT` to — defaults avoid the common 80/443/8080/8443).
Your browser must hold a client cert issued by the CA in `internal_ca.crt`.

### LLM backend (Argus + planning)

Daedalus calls any OpenAI-compatible `/v1/chat/completions` endpoint via
`LLM_BASE_URL`. Recommended on a Blackwell / GB10 box:

- **vLLM** with NVFP4 — best perf, easy to spin up, native OpenAI shape.
- **NVIDIA NIM** — NVIDIA's blessed container path.
- **Ollama** with `/v1` — easiest for laptops, slower on Blackwell.

Suggested models in 2026:

- `Qwen/Qwen2.5-Coder-32B-Instruct` (default in `.env.example`)
- `Qwen/Qwen3-Coder-30B-Instruct`
- The cached `nvidia/Gemma-4-31B-IT-NVFP4` works out of the box if you
  point vLLM at it.

If `LLM_BASE_URL` is unreachable Daedalus falls back to a deterministic
verdict (Argus) / 1-idea-1-task split (planning) so dev environments
without a model server still work.

#### Bundled vLLM (`--profile llm`)

If you'd rather have everything live in one `docker compose` and don't
already run inference somewhere, bring up the bundled vLLM service:

```bash
make llm.up      # docker compose --profile llm up -d llm
make llm.logs    # tails model-load + serving logs
make llm.models  # GET /v1/models from inside the container
make llm.down
```

`make llm.up` and the regular `make up` stack stack — they run on the
shared `backnet` network, so the API/Hermes/Talos resolve `llm` and
`LLM_BASE_URL=http://llm:8000/v1` works out of the box.

Requirements:

- Docker 24+ with the **NVIDIA Container Toolkit** installed and the
  `nvidia` runtime available. CPU-only hosts should leave the profile
  off and point `LLM_BASE_URL` at a remote endpoint instead.
- Enough VRAM for the chosen `LLM_MODEL`. The defaults assume a single
  Blackwell-class GPU; tune `VLLM_TENSOR_PARALLEL_SIZE`,
  `VLLM_MAX_MODEL_LEN`, and `VLLM_GPU_MEMORY_UTILIZATION` in `.env`
  for your hardware.
- The first start downloads the model into the `daedalus_hf_cache`
  named volume. Cold starts on a 32B model take a few minutes; the
  compose health check has a 600 s `start_period` to cover this. Set
  `HF_TOKEN` if you pick a gated model.

Pass extra vLLM CLI flags via `VLLM_EXTRA_ARGS`, e.g.
`VLLM_EXTRA_ARGS="--trust-remote-code --enforce-eager"`.

## Current MVP surface

- Browser control room served by the API at `/`
- 3-step login: password → email OTP → TOTP (recovery codes accepted)
- REST API under `/api/v1`:
  - projects, tasks, ideas, notes
  - connectors (with JSON-Schema validation)
  - **plans** — Plan Review flow: drafts produced by the planning job land in
    `/api/v1/projects/:id/plans?status=pending` and become real tasks via
    `/api/v1/plans/:plan_id/confirm` (or are dropped via `/discard`)
  - runs (pause / resume / interrupt / kill / detach / inject), transcripts,
    Argus reports
  - **snapshots** — `GET /api/v1/runs/:rid/snapshot`, `POST /api/v1/runs/:rid/rollback`
    (yolo-profile runs are snapshotted automatically with a `daedalus-snap/<run_id>`
    git tag before they execute)
  - audit log (owner only)
- Single-runner queue via Hermes with priority lanes (urgent / default / bg)
- Talos PTY agent supervisor — wall-clock and **idle-output** timeouts,
  per-run worktree, transcript persistence to S3/MinIO
- Argus verification loop with **no-progress detection** — same diff hash
  on consecutive fix attempts halts the loop instead of spinning
- Iris realtime websockets (all session-cookie + cert-fingerprint authenticated):
  - `wss://.../ws/pty/{run_id}`
  - `wss://.../ws/projects/{project_id}/events`
  - `wss://.../ws/queue`

## Default connector pack

Imported by `make seed-connectors`:

| ID                      | Profile  | Notes                                         |
|-------------------------|----------|-----------------------------------------------|
| `claude-code-confirm`   | confirm  | Claude Code with permission prompts           |
| `claude-code-yolo`      | yolo     | Claude Code with `--dangerously-skip-permissions` |
| `claude-multi-confirm`  | confirm  | Claude multi-agent variant                    |
| `claude-multi-yolo`     | yolo     | Claude multi-agent, full permissions          |
| `qwen-coder-confirm`    | confirm  | Qwen Coder CLI                                |
| `qwen-coder-yolo`       | yolo     | Qwen Coder CLI, full permissions              |
| `codex-confirm`         | confirm  | Codex CLI, approval-style                     |
| `codex-yolo`            | yolo     | Codex CLI with `--auto-approve`               |
| `shell-demo`            | confirm  | Plain `bash` smoke connector for testing      |

## Subsystems

| Codename     | Component                                              |
|--------------|--------------------------------------------------------|
| Daedalus     | API gateway + orchestration (`backend/daedalus/api`)   |
| Cerberus     | Auth: password + email OTP + TOTP (`backend/daedalus/auth`) |
| Hermes       | Queue / scheduler (`backend/daedalus/hermes`)          |
| Talos        | Single-runner PTY agent supervisor (`backend/daedalus/talos`) |
| Argus        | Verification loop (in-scheduler post-process + structured findings) |
| Hephaestus   | Build/test runner used by Argus (connector `verify_commands`) |
| Mnemosyne    | Persistence (Postgres + MinIO + Redis)                  |
| Iris         | WebSocket fan-out (`backend/daedalus/iris`)            |

## License

MIT — see [`LICENSE`](./LICENSE).
