# 06 — Connectors & LLM Integration

Source: `backend/daedalus/connectors/`, `backend/daedalus/llm/`, `connectors/*.json`.

## Connector system

- **JSON-Schema-validated** specs (Draft 2020-12): `id`, `display_name`, `command`,
  `workdir`, `permission_profile`, `input_format`, `done_signal` (`connectors/schema.py`).
- **Permission profiles**: `confirm` (interactive approval) vs `yolo` (autonomous + pre-run
  snapshot). Separate read-only `argus_profile` for verification runs.
- **`verify_commands`** — post-run build/test commands (Hephaestus), captured + reported.
- **`usage_parser`** kinds: `claude` / `openai` / `regex` / `json_block` → token counts +
  cost from transcript (`connectors/usage.py`).
- **Input format**: templated stdin or arg prompt with `{{task.title}}`,
  `{{task.description}}`, `{{task.acceptance_criteria}}`; `prompt_as_arg` flag.
- **Done signal**: regex / exit_code / tool_call (`oneOf`).
- **`egress_allowlist`** — hostnames forwarded to the agentnet firewall.
- **Resource limits** per connector: CPU shares, memory MB, PID ceiling, wall-clock,
  idle timeout; interrupt signals + grace.
- **Tags** for categorization.

### Management API

- `GET /api/v1/connectors?include_disabled=`, `POST /api/v1/connectors` (upsert + schema
  validate), `PATCH .../{cid}/overrides`, `enable`, `disable`, `DELETE .../{cid}`.
- `POST /api/v1/connectors/reload` — **hot-reload** on-disk pack (owner-only); invalid specs
  abort with 400, DB untouched (`connectors/loader.py`).
- **Operator overrides** (`connectors/overrides.py`): `force_project_overrides` injects
  `override_planning_model` / `override_task_model` / `override_verifier_model` /
  `override_wall_clock_minutes` / `override_argus_enabled` / `override_max_fix_loops` into
  every project using the connector.

## Default connector pack

| ID | Profile | Notes |
|----|---------|-------|
| `claude-code-confirm` | confirm | Claude Code with permission prompts |
| `claude-code-yolo` | yolo | `--dangerously-skip-permissions` |
| `claude-code-interactive` | confirm | operator-driven interactive |
| `claude-code` | — | baseline profile |
| `claude-multi-confirm` | confirm | multi-agent (≤4 subagents), higher limits |
| `claude-multi-yolo` | yolo | multi-agent, full permissions |
| `qwen-coder-confirm` / `-yolo` | confirm/yolo | Qwen Coder CLI, openai usage parser |
| `codex-confirm` / `-yolo` | confirm/yolo | Codex CLI, exit-code done signal |
| `shell-demo` | confirm | plain bash smoke connector |

## LLM integration

- Generic **OpenAI-compatible** `/v1/chat/completions` client (vLLM/NIM/Ollama/OpenAI or
  Anthropic via bundled LiteLLM proxy) (`llm/client.py`).
- **Dual backend** (`LLM_BACKEND`): `cli` (shells to local `claude --print --output-format
  json`, strips `ANTHROPIC_API_KEY` → OAuth subscription, zero API cost) or `http`
  (`llm/cli_backend.py`).
- `chat()` / `chat_json()` with JSON extraction + one reprompt retry; configurable
  temperature (0.2), max_tokens (2048), `response_format_json`.
- Separate `LLM_VERIFIER_MODEL`; configurable `LLM_TIMEOUT_SECONDS`,
  `LLM_MAX_DIFF_CHARS`, `LLM_MAX_LOG_CHARS`.
- Deterministic fallback (Argus verdict / 1-idea-1-task) when unreachable.
- Optional bundled **vLLM** service (`--profile llm`, NVIDIA GPU) and always-on LiteLLM
  Anthropic bridge (`deploy/docker-compose.yml`).
