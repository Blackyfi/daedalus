# Daedalus — Feature Documentation

**Daedalus** = *Distributed Agent Execution, Direction & Autonomous Lifecycle Unified System*.

A self-hosted web platform for orchestrating local AI coding agents (Claude Code, Codex,
Qwen, custom shells) against project-scoped task graphs. Single-runner queue, live terminal
mirroring, autonomous progress verification, idea→task review, pre-yolo workspace snapshots,
and 3-factor + mTLS auth.

This directory is the authoritative, exhaustive feature catalog. Each file documents one
subsystem with concrete capabilities and `file:line` references into the codebase.

## Subsystem map

| Codename     | Component                                              | Doc |
|--------------|--------------------------------------------------------|-----|
| Cerberus     | Auth: password + email OTP + TOTP + WebAuthn + mTLS    | [01-auth-security.md](./01-auth-security.md) |
| Daedalus     | API gateway, projects, discovery                       | [02-projects-discovery.md](./02-projects-discovery.md) |
| Daedalus     | Tasks, ideas, notes, LLM planning + plan review        | [03-tasks-ideas-planning.md](./03-tasks-ideas-planning.md) |
| Hermes / Talos / Argus | Scheduler, PTY runner, verifier, cgroups     | [04-orchestration-core.md](./04-orchestration-core.md) |
| Daedalus     | Runs, snapshots/rollback, merge & ship engine          | [05-runs-snapshots-merge.md](./05-runs-snapshots-merge.md) |
| Daedalus     | Connectors + LLM integration                           | [06-connectors-llm.md](./06-connectors-llm.md) |
| Daedalus / Iris | Frontend SPA + realtime websockets                  | [07-frontend-ui.md](./07-frontend-ui.md) |
| Pythia / Mnemosyne | Observability, analytics, CLI, deployment/ops    | [08-observability-cli-deploy.md](./08-observability-cli-deploy.md) |

## High-level capability summary

- **3-step + mTLS auth** — password (Argon2id) → email OTP → TOTP / WebAuthn, with
  per-account lockout, per-IP ban, cert pinning, and audit-log anomaly detection.
- **Project-scoped task graphs** — DAG dependencies, priorities, kanban board, idea box,
  LLM-generated plan proposals with human review.
- **Single-runner orchestration** — Hermes priority-lane queue (urgent/default/bg) with
  atomic Redis leases and one concurrent run per project.
- **PTY agent execution (Talos)** — real terminal, live mirroring, pause/resume/interrupt/
  kill/inject/resize, wall-clock + idle timeouts, per-run git worktree, cgroup limits.
- **Autonomous verification (Argus)** — LLM verdict (pass/partial/fail) + structured
  findings, no-progress diff-hash halting, fix-loop spawning with depth caps.
- **Snapshots & merge** — pre-yolo git-tag snapshots + rollback; batch merge with
  agent-driven conflict resolution and fast-forward ship.
- **Connectors** — JSON-schema-validated agent specs (confirm/yolo), verify commands,
  usage/cost parsing, hot-reload, operator overrides, egress allowlists.
- **Observability** — Prometheus metrics, Grafana/Loki/OTel stack, Pythia subscription
  oracle, KPI time-series dashboards.
- **Ops** — three-network docker-compose, Caddy mTLS proxy, agentnet egress firewall,
  Postgres backups, management CLI.

## Status

Per `TODO.md`, the platform is **feature-complete against the v1 spec**. Remaining items
are explicit deferrals (e.g. connector signing) or v1.x nice-to-haves. The improvement
backlog in [`../QUALITY_PLAN.md`](../QUALITY_PLAN.md) targets v1.x → v2.

> See [`../../project-plan.md`](../../project-plan.md) for the original design spec and
> [`../../AUDIT_REPORT.md`](../../AUDIT_REPORT.md) for the most recent audit.
