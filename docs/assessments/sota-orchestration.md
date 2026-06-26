# Daedalus vs. the State of the Art in AI Coding-Agent Orchestration

**Assessment date:** 2026-06-26
**Author:** Competitive/SOTA analysis (web-grounded)
**Subject:** Daedalus — self-hosted platform orchestrating *local* AI coding agents (Claude Code, Codex, Qwen, shells) against project-scoped task graphs.

> **How to read this.** For each of the eight dimensions the brief requested, Daedalus is rated **LEAD** (ahead of most/all peers), **PARITY** (comparable to the strong field), or **LAG** (behind the field), followed by the concrete upgrade that closes the gap. All competitor claims carry inline source URLs. Post-Jan-2026 model names and a few fast-moving prices are flagged where a primary source could not be confirmed.

---

## 0. Executive summary

Daedalus is a genuinely strong **self-hosted, privacy-first, vendor-neutral** orchestrator. Its moat is the combination almost no competitor has all of: local agents + OAuth-subscription (zero-API-cost) verification + per-connector egress firewall + hard per-project cost caps + a sophisticated LLM verifier with no-progress halting and a batch-merge/ship engine. On **self-hosting, cost governance, live PTY intervention, and autonomous quality-gating**, Daedalus leads or ties the best.

Where it is behind is **parallelism** (single-runner-per-project while the entire field has moved to parallel worktree/container fan-out and best-of-N) and **forge integration** (Daedalus is local-git-only; it creates no GitHub/GitLab PRs, ingests no CI signal, posts no review comments). Sandboxing is mid-pack: stronger than worktree-only desktop tools because of cgroups + egress firewall + network segmentation, but weaker than the container-per-agent isolation that Sculptor and container-use now ship.

A notable market backdrop: this niche churned hard in H1 2026 — **Terragon shut down (Jan 2026), Crystal deprecated (Feb 2026), Roo Code archived (May 2026), Vibe Kanban's parent Bloop shut down (Apr 2026), HumanLayer pivoted** ([Terragon](https://github.com/terragon-labs/terragon-oss), [Crystal](https://github.com/stravu/crystal), [Roo Code archived](https://api.github.com/repos/RooCodeInc/Roo-Code), [Vibe Kanban shutdown](https://www.vibekanban.com/blog/shutdown), [HumanLayer](https://github.com/humanlayer/humanlayer)). The durable players are well-capitalized (Cognition ~$26B, Factory ~$1.5B, Cursor) or are platform incumbents (GitHub, Google, OpenAI). Daedalus's defensible angle is the one none of them serve well: **air-gapped, self-hosted orchestration of local subscription agents with hard cost control.**

### Top 8 gap-closing upgrades, ranked by impact

1. **Parallel intra-project fan-out + best-of-N ensemble.** Replace strict single-runner-per-project with N concurrent per-task worktrees and an optional "race the same task across models/profiles, keep the winner" mode. This is the single biggest gap: *every* serious peer ships it (Cursor `/best-of-n` + 25 worktrees, Codex `--attempts N`, Devin-manages-Devins, Factory worktree fan-out, Conductor/Crystal/Claude Squad parallel worktrees).
2. **Forge integration (GitHub/GitLab PRs + CI gating + review comments).** Push run branches to a remote, open PRs with generated descriptions, gate ship on CI status checks, and accept `@daedalus`-style comment triggers. Daedalus is currently local-git-only; this is table stakes for any team and is the second-largest gap.
3. **Container-per-agent sandbox isolation.** Move yolo runs from "uid + cgroups + shared worktree on agentnet" to a real container per run (Docker/Dagger/container-use-style), so `--dangerously-skip-permissions` is actually contained. Matches Sculptor and container-use; strengthens the self-host security pitch.
4. **Test-generation-before-fix + anti-"fake-green" audits in Argus.** Have Argus (or a pre-fix step) write a failing test that encodes the acceptance criteria before the fix agent runs, and add an audit that flags "tests passed" claims unsupported by actually-executed tests. This sharpens Daedalus's existing verifier moat to match Sculptor's Instruction Audits and Aider's auto-test loop.
5. **Coordinator / sub-agent decomposition across worktrees.** A planner run that spawns dependency-aware child task-runs in parallel worktrees and reconciles them — turning the existing DAG + plan-proposal machinery into true multi-agent fan-out (Devin-manages-Devins, Cline Multi-Agent Teams). Builds directly on #1.
6. **CI-failure ingestion into the fix-loop.** Feed remote CI/check failures (and local `verify_commands` failures) back as structured Argus findings that spawn fix-tasks — the loop Jules's "CI Fixer," Copilot, and Conductor's "forward failing checks" already close.
7. **Spec-driven planning, mid-run plan checkpoints, and persistent project knowledge.** Add a spec/plan-approval gate that can pause *mid-run* (Devin Interactive Planning, Jules plan-approval + Planning Critic, Factory `--use-spec`) plus reusable per-project "knowledge/playbooks" (Devin Knowledge/Playbooks, OpenHands microagents, `AGENTS.md`).
8. **Inline code-review surface.** Render Argus structured findings as inline diff/PR review comments with accept/steer affordances, so verification output is actionable in the same place humans review (Cursor Bugbot, Copilot code review, Codex `@codex review`, Charlie PR Helper).

---

## 1. What Daedalus actually is (grounded capsule)

From the feature docs (`docs/features/`):

- **Queue/scheduler (Hermes):** priority lanes (urgent/default/bg), **single-runner-per-project** via Redis Lua project lease, `max_concurrent_projects = 4` (so up to 4 *projects* run at once, one runner each), DAG dependency gating, orphan recovery, connector-level rate-limit pause, per-lane Prometheus gauges (`04-orchestration-core.md`).
- **PTY runner (Talos):** real PTY (40×160, resizable), full lifecycle signalling (pause/resume/interrupt/kill/detach/**inject stdin**/resize) over the whole process group, per-run git worktree on `daedalus-run-<id>`, auto-commit on success, live output to Redis stream, transcript to S3/MinIO, per-connector usage→cost parsing, cgroups v2 limits (`04`).
- **Verifier (Argus):** LLM verdict pass/partial/fail + structured findings (severity/category/evidence) + suggested fix-task; **phantom-commit guard**, **SHA-256 no-progress diff-hash halting**, **fix-loop spawning with depth-cap → manual-review**, analytical-task empty-diff allowance, deterministic fallback (`04`).
- **Runs/snapshots/merge:** pre-yolo `daedalus-snap/<run_id>` git tag + `git reset --hard` rollback, retry-as-fresh-run chain, **batch-merge engine** (preview via `git merge-tree`, sequential `--no-ff`, **agent-driven conflict resolution**, **fast-forward ship**), `require_argus_pass` filter (`05`).
- **Connectors/LLM:** JSON-Schema specs, **confirm vs yolo** profiles + read-only `argus_profile`, `verify_commands`, usage parsers (claude/openai/regex/json_block), **`egress_allowlist`**, per-connector resource limits, operator overrides, hot-reload. Dual LLM backend incl. **CLI backend that strips the API key to use OAuth subscription at zero API cost** (`06`).
- **Planning:** LLM plan proposals from ideas/repo context → **human review (confirm/edit/discard)**, DAG resolution, deterministic fallback (`03`).
- **Ops/safety:** three Docker networks (front/back/**agentnet egress-filtered**), **agentnet firewall sidecar** rewriting the host `DOCKER-USER` chain from connector allowlists, mTLS + TOTP auth, **monthly + per-project cost caps**, **Pythia** subscription oracle (Claude OAuth 5h/weekly utilization), Prometheus/Grafana/Loki, pg-backup (`08`).

The key architectural facts for positioning: **isolation = per-run git worktree + uid + cgroups + network-segment egress firewall, not a container per agent**; **concurrency = one runner per project, ≤4 projects** (plus `claude-multi` connectors allowing ≤4 subagents *within* one run); **VCS = local git only, no remote forge**.

---

## 2. Per-dimension analysis

### 2.1 Multi-agent concurrency & parallelism — **LAG**

**Field.** Parallel fan-out is now universal. Cursor gives each agent an isolated worktree (default cap **25/machine**) and `/best-of-n` runs one task across models in parallel ([Cursor worktrees](https://cursor.com/docs/configuration/worktrees)). OpenAI Codex runs parallel tasks in isolated containers with `--attempts N` best-of-N and built-in subagents ([Codex CLI](https://developers.openai.com/codex/cli/features)). Devin can now spawn **parallel managed Devins**, each in its own VM, coordinated by a parent ([Devin manages Devins](https://cognition.ai/blog/devin-can-now-manage-devins)). Factory runs worktree-parallel droids (`droid --worktree a & droid --worktree b`) plus cloud "Droid Computers" ([Factory CLI](https://docs.factory.ai/reference/cli-reference)). Among local managers, Conductor, Crystal, Claude Squad (tmux+worktree), uzi (`--agents claude:2,codex:1` fan-out), and Vibe Kanban (10+ backends in parallel) all do per-task parallel worktrees; Cline ships a **Multi-Agent Teams SDK** with a coordinator delegating to specialists in parallel ([Cline Teams](https://docs.cline.bot/sdk/guides/multi-agent-teams)). SWE-agent's SWE-ReX runs 100+ agents in parallel for eval ([SWE-ReX](https://swe-rex.com/latest/)).

**Daedalus.** Deliberately single-runner-per-project (clean lease semantics, no intra-project write contention) with up to 4 projects concurrent, plus ≤4 subagents inside a `claude-multi` run. This is safe and simple but caps throughput and offers no best-of-N or task-level fan-out within a project.

**Gap-closing upgrade.** Introduce per-task worktree parallelism within a project (the worktree-per-run plumbing already exists), gated by a concurrency setting and a write-conflict policy, plus a "race N models/profiles, keep the Argus-best" ensemble mode. This is upgrade **#1** and the highest-impact change.

### 2.2 Task/plan decomposition & human-review ergonomics — **PARITY**

**Field.** Jules has the most explicit gate: it presents a plan and **waits for approval before changing code**, with a "Planning Critic" reviewing auto-approved plans ([Jules changelog](https://jules.google/docs/changelog/)). Devin's Interactive Planning + **Planning Checkpoints surface the plan before ACUs are consumed**, with persistent **Knowledge** and reusable **Playbooks** ([Devin 2](https://cognition.com/blog/devin-2)). Factory has `--use-spec` plan-before-execute ([Factory GA](https://factory.ai/news/factory-is-ga)). Cursor/Codex have Plan modes; Cline has Plan/Act with a persistent Focus Chain ([Cline Plan/Act](https://docs.cline.bot/features/plan-and-act)); Backlog.md specializes in acceptance-criteria + Definition-of-Done decomposition ([Backlog.md](https://github.com/MrLesk/Backlog.md)). OpenHands microagents and the `AGENTS.md` convention provide persistent repo knowledge.

**Daedalus.** Solid: LLM plan proposals from ideas + repo context, DAG dependencies, human confirm/edit/discard, deterministic fallback (`03`). What it lacks vs the leaders: a **mid-run** plan checkpoint (its approval is pre-run only), a spec mode, and **persistent reusable project knowledge/playbooks**.

**Gap-closing upgrade.** Add (a) a mid-run "plan checkpoint" pause that can re-plan before consuming more budget, and (b) a per-project knowledge/playbook store injected into prompts (upgrade **#7**).

### 2.3 Verification / autonomous quality-gating — **LEAD (tied with Sculptor)**

**Field.** Most tools stop at human diff review. The standouts: **Sculptor's** Suggestions and Instruction Audits flag misleading behavior like "'tests passed' without real tests" and CLAUDE.md violations, and its `fix-bug` skill **writes a failing test first** ([Sculptor product](https://imbue.com/product/sculptor/)). **Aider** runs auto-lint + auto-test with an auto-fix loop ([Aider lint/test](https://aider.chat/docs/usage/lint-test.html)). Jules has an adversarial "critic" + CI Fixer ([Jules critic](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/)); Codex is RL-trained to run tests until passing and flags P0/P1 in review ([Codex code review](https://developers.openai.com/codex/cloud/code-review)); Devin self-reviews PRs. SWE-bench Verified remains the headline eval but is now criticized for saturation/contamination — OpenAI **stopped reporting it ~Feb 2026** ([Tessl](https://tessl.io/blog/openai-moves-beyond-swe-bench-verified-as-coding-benchmarks-saturate/)), and the "SWE-Bench Illusion" paper shows models solving it without the codebase ([arXiv 2506.12286](https://arxiv.org/abs/2506.12286)). Harder successors: SWE-bench Pro (top public ~59%, [Scale](https://labs.scale.com/leaderboard/swe_bench_pro_public)), SWE-bench-Live, SWE-rebench.

**Daedalus.** Argus is genuinely advanced for this class: structured pass/partial/fail findings, **phantom-commit guard**, **no-progress diff-hash halting**, **depth-capped fix-loop spawning → manual-review**, plus `verify_commands` (build/test) captured and fed in (`04`, `06`). Few competitors have *all* of: deterministic anti-spoofing guards + automatic no-progress halting + bounded autonomous fix loops.

**Gap-closing upgrade.** The one missing piece vs Sculptor/Aider is **test-first verification**: have Argus synthesize a failing test from the acceptance criteria before the fix agent runs, and add an explicit "fake-green" audit that cross-checks claimed test success against actually-executed `verify_commands` output (upgrade **#4**). Also consider a small self-hosted regression-eval harness so operators can score connectors over time.

### 2.4 Version-control integration — **LAG**

**Field.** This is where Daedalus is most clearly behind. Devin Review covers GitHub (incl. Enterprise Server) and **GitLab self-managed** with logical diff grouping and auto-fix ([Devin Review](https://docs.devin.ai/work-with-devin/devin-review)). Copilot's coding agent opens **draft PRs**, runs Copilot code review on Actions runners, and enforces strong guardrails (only `copilot/` branches, **cannot approve/merge its own PR**, CI requires a human "Approve and run workflows") ([Copilot risks](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)). Cursor supports GitHub/GitLab/Azure/Bitbucket with a git egress proxy and Bugbot inline PR comments ([Cursor Bugbot](https://cursor.com/bugbot)). Codex does `@codex` on issues/PRs and `@codex review`. Factory and Vibe Kanban open PRs with generated descriptions; Charlie's PR Helper resolves merge conflicts and CI failures ([Charlie](https://charlielabs.ai/blog/introducing-daemons/)).

**Daedalus.** Strong *local* git: per-run worktrees, auto-commit, a batch-merge engine with agent-driven conflict resolution, and fast-forward ship (`05`). But it does **not** push to a remote forge, open PRs, post inline review comments, or gate on remote CI. For solo/air-gapped use the local merge/ship engine is arguably *better* than one-PR-per-task tools; for team workflows the absence of forge integration is a hard blocker.

**Gap-closing upgrade.** Add an optional forge connector: push run branches, open PRs with Argus-summary descriptions, gate `ship` on CI status checks, render Argus findings as inline review comments, and accept comment-mention triggers (upgrades **#2**, **#6**, **#8**). Keep it optional so the air-gapped story is preserved.

### 2.5 Live observability & intervention — **LEAD**

**Field.** Devin offers a live web view (shell/editor/browser) + replay; Copilot's Mission Control streams real-time logs across tasks ([Mission Control](https://github.blog/changelog/2025-10-28-a-mission-control-to-assign-steer-and-track-copilot-coding-agent-tasks/)); Codex exposes `turn/steer` + `streamStdoutStderr`; Cursor/Jules allow mid-run follow-up messages. container-use has `cu watch` and `cu terminal` to drop into a running agent's container ([container-use](https://github.com/dagger/container-use)); Conductor has a "Big Terminal Mode." But **true multi-attach PTY mirroring with input handoff** is rare.

**Daedalus.** A real PTY mirrored live to all viewers with **multi-attach input handoff** (`inject` stdin, audited), plus pause(SIGSTOP)/resume/interrupt/kill/resize over the process group, live Redis output stream, transcript persistence, and per-run token/cost display (`04`, `05`). Combined with Prometheus/Grafana/Loki and the Pythia subscription oracle, the live-intervention story is best-in-class for self-hosted local agents.

**Gap-closing upgrade.** Mostly a lead to defend. Incremental wins: a cross-run "mission-control"-style dashboard (one pane for all active runs across projects), and queued follow-up messages that deliver at the next agent turn (Codex Steer-mode ergonomics).

### 2.6 Safety / sandboxing — **PARITY (mixed: leads on egress, lags on process isolation)**

**Field.** Cloud agents lead on isolation: Codex sandboxes with Seatbelt/bubblewrap+seccomp and **network off by default** with an egress allowlist proxy ([Codex sandboxing](https://developers.openai.com/codex/concepts/sandboxing)); Cursor cloud agents run in isolated AWS VMs with three egress modes + git proxy ([Cursor network](https://cursor.com/docs/cloud-agent/security-network.md)); Copilot runs in ephemeral Actions envs behind a **default-deny firewall** (caveat: the firewall covers only the Bash tool, not MCP/setup steps) ([Copilot firewall](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall)); Devin uses ephemeral VMs with default-deny egress + snapshot rollback. Among local tools, **only Sculptor and container-use give a real container per agent** ([Sculptor containers](https://imbue.com/blog/containers), [container-use](https://github.com/dagger/container-use)); the rest (Conductor, Crystal, Claude Squad, uzi, Vibe Kanban, Cline, Aider) rely on worktree or shadow-git isolation and frequently default to `--yolo`/`--dangerously-skip-permissions` — weak.

**Daedalus.** Better than the worktree-only desktop pack: **per-connector egress allowlist enforced by a host firewall sidecar** rewriting `DOCKER-USER`, three-network segmentation (agentnet), cgroups v2 limits, uid isolation, confirm/yolo profiles, and **pre-yolo git-tag snapshots + rollback** (`05`, `06`, `08`). The egress-control + snapshot story actually *leads* most local managers. But agents still run as processes (uid+cgroups) sharing the agentnet host stack rather than each in its own container, so a yolo run is less contained than Sculptor/container-use.

**Gap-closing upgrade.** Add an optional **container-per-run** backend (Docker or Dagger/container-use) so yolo profiles get filesystem + process isolation on top of the existing egress firewall and snapshots (upgrade **#3**).

### 2.7 Self-hosting / on-prem / privacy — **LEAD**

**Field.** Almost everyone is cloud-first. Jules and Charlie are **cloud-only, no on-prem** ([Jules FAQ](https://jules.google/docs/faq/), [Charlie "End of Local"](https://charlielabs.ai/blog/the-end-of-local/)); Copilot's agent is GitHub-hosted only; Devin offers VPC ("Customer Dedicated Deployment") but full self-host appears to be in flux and is not air-gapped ([Devin deployment](https://docs.devin.ai/enterprise/deployment/overview)). The exceptions: **Cursor** now ships self-hosted, **air-gapped** cloud agents via Helm/K8s ([Cursor self-hosted](https://cursor.com/blog/self-hosted-cloud-agents)); **Factory** offers SaaS/Hybrid/On-Premise/**Air-Gapped** with SOC 2 + ISO 27001 + ISO 42001 ([Factory pricing](https://docs.factory.ai/pricing)); OpenHands is fully self-hostable (MIT); container-use/Sculptor/Claude Squad/Cline/Aider are local-first OSS.

**Daedalus.** Fully self-hosted by design: Docker-Compose stack, mTLS + TOTP, BYO local models (vLLM/NIM/Ollama) or Anthropic via a bundled LiteLLM bridge, and the **CLI backend that uses the Claude OAuth subscription at zero API cost** (`06`, `08`). This is a top-tier privacy/on-prem posture, in the same bracket as Factory and Cursor's air-gapped tier and ahead of every cloud-only competitor — and unique in being built around *local subscription* agents rather than metered APIs.

**Gap-closing upgrade.** Defend the lead and make it legible: document an air-gap deployment profile explicitly, and (for enterprise credibility) pursue the compliance attestations (SOC 2 / ISO) that Factory uses as a wedge. No core capability gap here.

### 2.8 Cost transparency & budgeting — **LEAD**

**Field.** Per-task/per-run token+cost *display* is common (Conductor, Vibe Kanban, Cline, Roo Code, Factory `/cost`, Aider `/tokens`), but **hard budget caps that auto-stop are nearly universally absent** — a spend-limit/auto-stop is an *open, unshipped feature request* in both Cline ([#4540](https://github.com/cline/cline/issues/4540)) and (former) Roo Code. GitHub added usage-based **AI Credits** with budget caps at enterprise/cost-center/user levels (June 2026, [budgets](https://docs.github.com/en/copilot/concepts/billing/budgets-for-usage-based-billing)); Devin added Review spend limits + per-project cost caps; SWE-agent has a per-instance `$3` default cap. Most metered cloud tools control cost via rate-limit windows, not true budgets.

**Daedalus.** Leads the local/self-hosted field: per-run `cost_usd_micros` from connector usage parsers, **monthly cost cap and per-project cost cap enforced at enqueue time**, the Pythia **subscription oracle** tracking Claude OAuth 5-hour/weekly utilization with reset countdowns, and connector-level rate-limit pause (`03`, `06`, `08`). The subscription-window awareness is something no competitor tracks. Hard caps + subscription tracking + zero-API-cost verification is a distinctive combination.

**Gap-closing upgrade.** Defend the lead; incremental wins: budget caps at the per-connector and per-lane level, projected-cost-to-complete estimates on the plan-review screen, and alerting before a cap is hit rather than only blocking at enqueue.

---

## 3. Dimension scorecard

| Dimension | Daedalus | Closest peers that beat/tie it | Verdict |
|---|---|---|---|
| Multi-agent concurrency & parallelism | Single-runner/project, ≤4 projects, ≤4 subagents/run | Cursor, Codex, Devin, Factory, Cline, Conductor, Claude Squad | **LAG** |
| Plan decomposition & human review | LLM proposals + DAG + confirm/edit/discard | Jules, Devin, Factory (spec), Backlog.md | **PARITY** |
| Verification / autonomous gating | Argus: structured findings, no-progress halting, fix-loops, phantom-commit guard | Sculptor (ties), Aider, Jules critic | **LEAD** (tied) |
| Version-control integration | Local git only: worktrees, batch-merge, ship | Devin, Copilot, Cursor, Codex, Factory, Charlie | **LAG** |
| Live observability & intervention | Multi-attach PTY + full lifecycle control + cost | container-use, Conductor, Copilot Mission Control | **LEAD** |
| Safety / sandboxing | Egress firewall + cgroups + snapshots, no per-agent container | Sculptor, container-use (process isolation); Codex/Cursor/Devin (cloud) | **PARITY** |
| Self-hosting / on-prem / privacy | Full self-host, mTLS+TOTP, local models, OAuth zero-cost | Factory, Cursor air-gapped, OpenHands | **LEAD** |
| Cost transparency & budgeting | Per-run cost, monthly + per-project caps, subscription oracle | GitHub budgets, Devin caps | **LEAD** |

---

## 4. Competitor quick reference (status as of June 2026)

**Cloud / enterprise heavyweights**
- **Devin (Cognition)** — managed autonomous engineer; parallel managed Devins; Devin Review (GitHub+GitLab); VPC (not air-gapped); Pro $20 / Max $200 quota model. ~$26B valuation (May 2026). ([Devin 2](https://cognition.com/blog/devin-2), [pricing](https://devin.ai/pricing))
- **Factory.ai** — Droids; worktree fan-out + cloud Droid Computers; GitHub+GitLab; **SaaS/Hybrid/On-Prem/Air-Gapped**, SOC2/ISO27001/ISO42001; ~$1.5B. Closest enterprise analog on the self-host axis. ([Factory GA](https://factory.ai/news/factory-is-ga), [pricing](https://docs.factory.ai/pricing))
- **OpenHands / All-Hands** — MIT, fully self-hostable, model-agnostic, parallel delegation, GitHub Issue Resolver Action; ~78k stars. The OSS analog to compare architecture against. ([repo](https://github.com/OpenHands/OpenHands))

**Big-vendor cloud agents**
- **Cursor** — agent/plan/cloud modes, 25 worktrees + `/best-of-n`, Bugbot inline review, **self-hosted air-gapped agents**; Pro $20. ([modes](https://cursor.com/docs/agent/modes), [self-hosted](https://cursor.com/blog/self-hosted-cloud-agents))
- **GitHub Copilot coding agent** — issue→draft-PR, Mission Control, default-deny firewall, can't merge own PR; AI Credits billing (June 2026). Workspace folded in / sunset. ([about](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent))
- **Google Jules** — strongest plan-approval gate + Planning Critic + CI Fixer; **GitHub-only, cloud-only**, daily task caps; bundled in Google AI plans. ([docs](https://jules.google/docs), [changelog](https://jules.google/docs/changelog/))
- **OpenAI Codex (cloud)** — parallel isolated containers, `--attempts N`, granular CLI sandbox (net off by default), `@codex review`; local-first CLI; in ChatGPT Plus $20/Pro $100. Default model now `gpt-5.5` (flagged: post-cutoff). ([cloud](https://developers.openai.com/codex/cloud), [sandboxing](https://developers.openai.com/codex/concepts/sandboxing))

**Local "parallel Claude Code" managers (Daedalus's nearest neighbors)**
- **Sculptor (Imbue)** — container per agent, **Instruction Audits / fake-green detection**, Pairing Mode bidirectional sync; MIT-ish, local-first, free beta. The verifier/sandbox tool to benchmark against. ([Sculptor](https://imbue.com/sculptor/))
- **container-use (Dagger)** — Apache-2.0 MCP server giving each agent a container + git branch; `cu watch`/`cu terminal`. The reusable sandbox primitive Daedalus could adopt for upgrade #3. ([repo](https://github.com/dagger/container-use))
- **Conductor (Melty Labs, YC S24)** — Mac-only, free, parallel worktrees, Checks tab gating, PR create/edit; **no sandbox by default**. ([docs](https://www.conductor.build/docs/core/parallel-agents))
- **Claude Squad** — AGPL, tmux+worktree per agent, active (v1.0.19). **Cline** — Apache-2.0, Multi-Agent Teams SDK (real parallel), Plan/Act, shadow-git checkpoints; budget cap still unshipped. **Backlog.md** — MIT task/AC decomposition layer. **Aider** — Apache-2.0, architect mode + auto-test loop, release-stalled but active.
- **Defunct/pivoted in H1 2026:** Terragon (shut down Jan), Crystal (deprecated Feb → Nimbalyst), Roo Code (archived May), Vibe Kanban (Bloop shut Apr, now community), HumanLayer→CodeLayer (pivoted), uzi (dormant since June 2025).

---

## 5. Confidence & currency flags

- **Model names beyond Jan 2026** (GPT-5.5/5.4, Gemini 3.1 Pro, Claude Opus 4.7/4.8, "Mythos/Fable 5") come from web sources/aggregators and are **not independently verifiable here**; cited where used. SWE-bench Verified now reportedly sits ~80–95% at the top, but the official leaderboard was not directly fetchable and top entries disagree across aggregators — treat exact numbers as indicative, and note the benchmark is widely considered saturated/contaminated ([Tessl](https://tessl.io/blog/openai-moves-beyond-swe-bench-verified-as-coding-benchmarks-saturate/), [arXiv 2506.12286](https://arxiv.org/abs/2506.12286)).
- **Pricing** for Cursor (Pro+/Ultra split), Copilot (AI Credit allotments), Codex (April 2026 credit transition), and Devin (quota vs legacy ACU) shifted in 2026 and some figures are secondary-sourced — verify before quoting externally.
- **Shutdown/pivot facts** (Terragon, Crystal, Roo Code, Vibe Kanban/Bloop, HumanLayer) are primary-source-verified.
- **Daedalus capabilities** are grounded in `docs/features/` (code-referenced) as of this repo state; if the implementation has advanced past the docs, re-confirm the concurrency and forge-integration claims in particular.

---

## 6. Source index (primary)

Devin: [cognition.com/blog/devin-2](https://cognition.com/blog/devin-2) · [manage Devins](https://cognition.ai/blog/devin-can-now-manage-devins) · [Devin Review](https://docs.devin.ai/work-with-devin/devin-review) · [deployment](https://docs.devin.ai/enterprise/deployment/overview) · [pricing](https://devin.ai/pricing)
OpenHands: [github](https://github.com/OpenHands/OpenHands) · [pricing](https://www.openhands.dev/pricing) · [GitHub Action resolver](https://docs.openhands.dev/openhands/usage/run-openhands/github-action)
SWE-agent/bench: [SWE-agent](https://github.com/SWE-agent/SWE-agent) · [SWE-ReX](https://swe-rex.com/latest/) · [SWE-bench Verified](https://www.swebench.com/verified.html) · [SWE-Bench Illusion](https://arxiv.org/abs/2506.12286)
Cursor: [modes](https://cursor.com/docs/agent/modes) · [worktrees](https://cursor.com/docs/configuration/worktrees) · [Bugbot](https://cursor.com/bugbot) · [self-hosted agents](https://cursor.com/blog/self-hosted-cloud-agents) · [network](https://cursor.com/docs/cloud-agent/security-network.md)
Copilot: [about coding agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent) · [firewall](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall) · [risks](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations) · [Mission Control](https://github.blog/changelog/2025-10-28-a-mission-control-to-assign-steer-and-track-copilot-coding-agent-tasks/) · [budgets](https://docs.github.com/en/copilot/concepts/billing/budgets-for-usage-based-billing)
Jules: [docs](https://jules.google/docs) · [changelog](https://jules.google/docs/changelog/) · [critic](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/) · [usage limits](https://jules.google/docs/usage-limits/)
Codex: [cloud](https://developers.openai.com/codex/cloud) · [CLI features](https://developers.openai.com/codex/cli/features) · [sandboxing](https://developers.openai.com/codex/concepts/sandboxing) · [code review](https://developers.openai.com/codex/cloud/code-review)
Factory: [GA](https://factory.ai/news/factory-is-ga) · [pricing](https://docs.factory.ai/pricing) · [CLI](https://docs.factory.ai/reference/cli-reference)
Charlie: [how it works](https://charlielabs.ai/how-it-works/) · [Daemons](https://charlielabs.ai/blog/introducing-daemons/)
Conductor: [parallel agents](https://www.conductor.build/docs/core/parallel-agents) · [checks](https://www.conductor.build/docs/reference/checks) · [FAQ](https://www.conductor.build/docs/faq)
Sculptor: [product](https://imbue.com/product/sculptor/) · [containers](https://imbue.com/blog/containers) · [github](https://github.com/imbue-ai/sculptor)
container-use: [github](https://github.com/dagger/container-use) · [blog](https://dagger.io/blog/agent-container-use/)
Crystal: [github](https://github.com/stravu/crystal) · Vibe Kanban: [github](https://github.com/BloopAI/vibe-kanban) · [shutdown](https://www.vibekanban.com/blog/shutdown) · Terragon: [oss snapshot](https://github.com/terragon-labs/terragon-oss)
Claude Squad: [github](https://github.com/smtg-ai/claude-squad) · uzi: [github](https://github.com/devflowinc/uzi) · Backlog.md: [github](https://github.com/MrLesk/Backlog.md) · Cline: [Teams SDK](https://docs.cline.bot/sdk/guides/multi-agent-teams) · [Plan/Act](https://docs.cline.bot/features/plan-and-act) · Roo Code: [archived](https://api.github.com/repos/RooCodeInc/Roo-Code) · HumanLayer: [github](https://github.com/humanlayer/humanlayer) · Aider: [lint/test](https://aider.chat/docs/usage/lint-test.html) · [git](https://aider.chat/docs/git.html)
