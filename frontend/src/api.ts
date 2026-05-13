// Tiny fetch wrapper. Always sends cookies; throws on non-2xx with the
// server's `detail` if available.
export async function api<T = unknown>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers,
  });
  const text = await res.text();
  let payload: any = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!res.ok) {
    const detail =
      (payload && (payload.detail ?? payload.message)) || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload as T;
}

export const apiJson = <T = unknown>(
  path: string,
  body: unknown,
  init: RequestInit = {},
) =>
  api<T>(path, {
    ...init,
    method: init.method ?? "POST",
    body: JSON.stringify(body),
  });

// ---- types ----

export interface Project {
  id: string;
  name: string;
  description: string | null;
  workspace_path: string;
  git_default_branch: string;
  default_connector_id: string | null;
  max_fix_loops: number;
  auto_run_fix: boolean;
  archived: boolean;
  planning_model: string | null;
  task_model: string | null;
  verifier_model: string | null;
  argus_enabled: boolean;
  wall_clock_minutes_override: number | null;
  created_at: string;
  updated_at: string;
}

// Anthropic models surfaced in the project settings dropdowns. Keep in sync
// with what's actually available to the operator's `claude` CLI / LiteLLM.
export const ANTHROPIC_MODELS: { id: string; label: string; tier: string }[] = [
  { id: "claude-opus-4-7",    label: "Opus 4.7",    tier: "highest quality, slowest" },
  { id: "claude-sonnet-4-6",  label: "Sonnet 4.6",  tier: "balanced (recommended default)" },
  { id: "claude-haiku-4-5",   label: "Haiku 4.5",   tier: "fastest, cheapest — good for verification" },
];

export interface Task {
  id: string;
  project_id: string;
  parent_task_id: string | null;
  title: string;
  description: string;
  acceptance_criteria: string;
  status:
    | "backlog"
    | "ready"
    | "in_progress"
    | "verifying"
    | "needs_fixes"
    | "done"
    | "cancelled";
  priority: "P0" | "P1" | "P2" | "P3";
  connector_id: string | null;
  profile: string;
  depends_on: string[];
  tags: string[];
  estimated_minutes: number | null;
  fix_loop_count: number;
  created_at: string;
  updated_at: string;
}

export interface Idea {
  id: string;
  project_id: string;
  text: string;
  tags: string[];
  archived: boolean;
  sort_index: number;
  created_at: string;
}

export const updateIdea = (id: string, text: string): Promise<Idea> =>
  apiJson<Idea>(`/api/v1/ideas/${id}`, { text }, { method: "PATCH" });

// Project-idea — sits on the Projects page, *above* the per-project ideas.
// `new` becomes `promoted` when the user converts it into a real project.
export type ProjectIdeaStatus = "new" | "promoted" | "archived";

export interface ProjectIdea {
  id: string;
  owner_id: string;
  text: string;
  tags: string[];
  status: ProjectIdeaStatus;
  promoted_project_id: string | null;
  sort_index: number;
  created_at: string;
  updated_at: string;
}

export const updateProjectIdea = (
  id: string,
  patch: { text?: string; tags?: string[]; status?: ProjectIdeaStatus },
): Promise<ProjectIdea> =>
  apiJson<ProjectIdea>(`/api/v1/project-ideas/${id}`, patch, { method: "PATCH" });

export interface ProjectIdeaPromoteIn {
  name: string;
  description?: string | null;
  workspace_path: string;
  git_default_branch?: string;
  default_connector_id?: string | null;
  init_git?: boolean;
}

export interface Note {
  id: string;
  project_id: string;
  title: string;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface Connector {
  id: string;
  connector_id: string;
  display_name: string;
  spec: Record<string, any>;
  schema_version: number;
  enabled: boolean;
}

export interface Run {
  id: string;
  task_id: string | null;
  project_id: string;
  kind: "task" | "argus" | "planning" | "cleanup";
  state:
    | "queued"
    | "claimed"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "aborted_unsafe";
  lane: "urgent" | "default" | "bg";
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  token_input: number | null;
  token_output: number | null;
  cost_usd_micros: number | null;
  retry_of: string | null;
}

export interface ArgusReport {
  id: string;
  run_id: string;
  task_id: string;
  verdict: "pass" | "partial" | "fail";
  summary: string;
  findings: { severity: string; category: string; description: string; evidence: string | null }[];
  suggested_fix_task: { title: string; description: string; acceptance_criteria: string } | null;
  created_at: string;
}

export interface PlanProposal {
  id: string;
  project_id: string;
  status: "pending" | "confirmed" | "discarded";
  proposed_tasks: ProposedTask[];
  rationale: string;
  source_idea_ids: string[];
  confirmed_at: string | null;
  created_at: string;
}

export interface ProposedTask {
  title: string;
  description?: string;
  acceptance_criteria?: string;
  priority?: "P0" | "P1" | "P2" | "P3";
  suggested_connector?: string | null;
  depends_on?: number[];
  tags?: string[];
  source_idea_id?: string | null;
}

export interface Snapshot {
  id: string;
  project_id: string;
  run_id: string | null;
  git_tag: string | null;
  tarball_object_key: string | null;
  note: string | null;
  created_at: string;
}

export interface DiscoveredRepo {
  name: string;
  path: string;
  relative_path: string;
  default_branch: string;
  description: string;
  last_commit_at: string | null;
  has_uncommitted: boolean;
  already_registered: boolean;
}

export interface DiscoverRepoEntry {
  path: string;
  name?: string | null;
  description?: string | null;
  git_default_branch?: string | null;
  default_connector_id?: string | null;
}

export interface AuditEvent {
  id: string;
  at: string;
  actor_user_id: string | null;
  actor_ip: string | null;
  actor_cert_fp: string | null;
  action: string;
  target_kind: string | null;
  target_id: string | null;
  payload: Record<string, any>;
}

// Static config surfaced to the SPA (workspaces root for path auto-suggest, etc).
export interface SystemConfig {
  workspaces_root: string;
}

// Pythia subscription snapshot (cached, refreshed by Talos every PYTHIA_REFRESH_SECONDS).
export interface SubscriptionInfo {
  kind:
    | "ok"
    | "auth_required"
    | "cli_missing"
    | "timeout"
    | "unparsed"
    | "error"
    | "stale_or_missing";
  email: string | null;
  plan: string | null;
  plan_tier: string | null;
  weekly_used_pct: number | null;
  five_hour_used_pct: number | null;
  weekly_resets_in: string | null;
  five_hour_resets_in: string | null;
  raw_text: string;
  error: string | null;
  fetched_at: string | null;
}

// Per-project runner snapshot for the global runner bar.
export interface ActiveRunner {
  project_id: string;
  project_name: string;
  run_id: string;
  run_kind: string;
  task_title: string | null;
  started_at: string | null;
}

export interface RunnerSnapshot {
  max_concurrent_projects: number;
  active_count: number;
  active: ActiveRunner[];
}

// Per-project task counts. `by_status` keys mirror the TaskStatus enum.
export interface ProjectStats {
  by_status: Record<
    | "backlog"
    | "ready"
    | "in_progress"
    | "verifying"
    | "needs_fixes"
    | "done"
    | "cancelled",
    number
  >;
  total: number;
  last_activity_at: string | null;
}

export type ProjectStatsMap = Record<string, ProjectStats>;

// Git working-tree status — drives the "git pull required" banner.
export interface GitStatusInfo {
  is_git_repo: boolean;
  has_remote: boolean;
  behind_count: number;
  ahead_count: number;
  branch: string | null;
  upstream: string | null;
  fetch_failed: boolean;
  fetch_error: string | null;
  last_fetched_at: string | null;
  checked_at: string | null;
  error: string | null;
  needs_pull: boolean;
}

export type GitStatusMap = Record<string, GitStatusInfo>;
