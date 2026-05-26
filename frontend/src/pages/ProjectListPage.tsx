import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  GitStatusMap,
  Project,
  ProjectStatsMap,
  Run,
  RunnerSnapshot,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";
import DiscoverModal from "../components/DiscoverModal";
import ProjectCard from "../components/ProjectCard";

// Tasks in these statuses are what the per-project /run-all endpoint picks
// up — used both to size the buttons (so we can show "Run 5" instead of a
// dead "Run all") and to pre-empt the obvious empty-state click.
function eligibleCount(stats: ProjectStatsMap | undefined, projectId: string): number {
  const s = stats?.[projectId]?.by_status;
  if (!s) return 0;
  return (s.backlog ?? 0) + (s.ready ?? 0) + (s.needs_fixes ?? 0);
}

// 409 from the server when no tasks match. Treated as a no-op for the
// global Run-all button — empty projects shouldn't poison the result.
const NO_ELIGIBLE_RE = /no eligible tasks/i;

export default function ProjectListPage() {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const projects = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api("/api/v1/projects"),
  });
  const connectors = useQuery<Connector[]>({
    queryKey: ["connectors"],
    queryFn: () => api("/api/v1/connectors"),
  });
  const runners = useQuery<RunnerSnapshot>({
    queryKey: ["runner-snapshot"],
    queryFn: () => api("/api/v1/system/runners"),
    refetchInterval: 5_000,
  });
  const stats = useQuery<ProjectStatsMap>({
    queryKey: ["project-stats"],
    queryFn: () => api("/api/v1/projects/stats"),
    refetchInterval: 10_000,
  });
  const gitStatuses = useQuery<GitStatusMap>({
    queryKey: ["project-git-status"],
    queryFn: () => api("/api/v1/projects/git-status"),
    refetchInterval: 30_000,
  });
  const activeByProject = new Map(
    (runners.data?.active ?? []).map((a) => [a.project_id, a]),
  );

  const [form, setForm] = useState({
    name: "",
    description: "",
    workspace_path: "",
    default_connector_id: "",
  });
  const [discoverOpen, setDiscoverOpen] = useState(false);

  const create = useMutation({
    mutationFn: (body: typeof form) =>
      apiJson("/api/v1/projects", {
        ...body,
        default_connector_id: body.default_connector_id || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setForm({ name: "", description: "", workspace_path: "", default_connector_id: "" });
      flash("Project created", "success");
    },
    onError: (err: any) => flash(err.message || "Project create failed", "error"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(form);
  }

  // Per-card Run-all: a single mutation reused by every card. We track
  // `variables` to scope the pending state to the card the user clicked.
  const runOne = useMutation<Run[], Error, { pid: string; name: string }>({
    mutationFn: ({ pid }) =>
      apiJson<Run[]>(`/api/v1/projects/${pid}/run-all`, {}),
    onSuccess: (runs, vars) => {
      flash(
        `${vars.name}: queued ${runs.length} run${runs.length === 1 ? "" : "s"}`,
        "success",
      );
      qc.invalidateQueries({ queryKey: ["project-stats"] });
      qc.invalidateQueries({ queryKey: ["runner-snapshot"] });
    },
    onError: (err, vars) =>
      flash(`${vars.name}: ${err.message || "Run all failed"}`, "error"),
  });

  // Global Run-all: fire one POST per project in parallel, swallow the 409
  // "no eligible tasks" responses (they're informational, not failures),
  // surface anything else as a failure count.
  const runAllProjects = useMutation<
    { totalRuns: number; projectsKicked: number; failures: { name: string; reason: string }[] },
    Error,
    void
  >({
    mutationFn: async () => {
      const list = (projects.data ?? []).filter((p) => !p.archived);
      const results = await Promise.allSettled(
        list.map(async (p) => {
          const runs = await apiJson<Run[]>(
            `/api/v1/projects/${p.id}/run-all`,
            {},
          );
          return { name: p.name, runs };
        }),
      );
      let totalRuns = 0;
      let projectsKicked = 0;
      const failures: { name: string; reason: string }[] = [];
      results.forEach((r, i) => {
        if (r.status === "fulfilled") {
          totalRuns += r.value.runs.length;
          if (r.value.runs.length > 0) projectsKicked++;
        } else {
          const msg = r.reason?.message ?? String(r.reason);
          if (!NO_ELIGIBLE_RE.test(msg)) {
            failures.push({ name: list[i].name, reason: msg });
          }
        }
      });
      return { totalRuns, projectsKicked, failures };
    },
    onSuccess: ({ totalRuns, projectsKicked, failures }) => {
      qc.invalidateQueries({ queryKey: ["project-stats"] });
      qc.invalidateQueries({ queryKey: ["runner-snapshot"] });
      if (totalRuns === 0 && failures.length === 0) {
        flash("No eligible tasks across any project", "info");
        return;
      }
      const projectWord = projectsKicked === 1 ? "project" : "projects";
      const runWord = totalRuns === 1 ? "run" : "runs";
      if (failures.length === 0) {
        flash(
          `Queued ${totalRuns} ${runWord} across ${projectsKicked} ${projectWord}`,
          "success",
        );
      } else {
        const failed = failures.map((f) => f.name).join(", ");
        flash(
          `Queued ${totalRuns} ${runWord} across ${projectsKicked} ${projectWord}; ${failures.length} failed (${failed})`,
          "error",
        );
      }
    },
    onError: (err) => flash(err.message || "Run all failed", "error"),
  });

  const totalEligible = (projects.data ?? [])
    .filter((p) => !p.archived)
    .reduce((acc, p) => acc + eligibleCount(stats.data, p.id), 0);

  function confirmRunAllProjects() {
    if (totalEligible === 0) {
      flash("No tasks in backlog, ready, or needs-fix across any project", "info");
      return;
    }
    const projectsWithWork = (projects.data ?? []).filter(
      (p) => !p.archived && eligibleCount(stats.data, p.id) > 0,
    ).length;
    if (
      window.confirm(
        `Queue ${totalEligible} task${totalEligible === 1 ? "" : "s"} across ${projectsWithWork} project${projectsWithWork === 1 ? "" : "s"} for execution?\n\n` +
          "Each project runs at most one task at a time, but multiple projects run in parallel up to MAX_CONCURRENT_PROJECTS. " +
          "Each task will spend Claude subscription quota.",
      )
    ) {
      runAllProjects.mutate();
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-6">
      <section className="panel lg:col-span-2">
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm uppercase tracking-wide text-muted">Projects</h2>
          <div className="flex items-center gap-3 text-xs text-muted">
            <button
              className="btn btn-primary"
              onClick={confirmRunAllProjects}
              disabled={runAllProjects.isPending || totalEligible === 0}
              title={
                totalEligible === 0
                  ? "No tasks in backlog, ready, or needs-fix"
                  : `Queue ${totalEligible} task${totalEligible === 1 ? "" : "s"} across every project`
              }
            >
              {runAllProjects.isPending
                ? "Queuing…"
                : totalEligible > 0
                  ? `▶ Run all (${totalEligible})`
                  : "▶ Run all"}
            </button>
            <button className="btn" onClick={() => setDiscoverOpen(true)}>
              Discover repos
            </button>
            <span>{projects.data?.length ?? 0}</span>
          </div>
        </header>
        {projects.isLoading && <p className="text-muted text-sm">Loading…</p>}
        {projects.data?.length === 0 && (
          <p className="text-muted text-sm">No projects yet — create one →</p>
        )}
        <div className="grid gap-2">
          {projects.data?.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              stats={stats.data?.[p.id]}
              activeRun={activeByProject.get(p.id)}
              gitStatus={gitStatuses.data?.[p.id]}
              runAllEligible={eligibleCount(stats.data, p.id)}
              runAllPending={
                runOne.isPending && runOne.variables?.pid === p.id
              }
              onRunAll={() =>
                runOne.mutate({ pid: p.id, name: p.name })
              }
            />
          ))}
        </div>
      </section>

      <section className="panel">
        <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">New project</h2>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="label">Name</label>
            <input
              className="field"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea
              className="field"
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Workspace path</label>
            <input
              className="field"
              value={form.workspace_path}
              onChange={(e) => setForm({ ...form, workspace_path: e.target.value })}
              placeholder="/workspaces/my-repo"
              required
            />
          </div>
          <div>
            <label className="label">Default connector</label>
            <select
              className="field"
              value={form.default_connector_id}
              onChange={(e) => setForm({ ...form, default_connector_id: e.target.value })}
            >
              <option value="">(none)</option>
              {connectors.data?.map((c) => (
                <option key={c.connector_id} value={c.connector_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </div>
          <button className="btn btn-primary w-full" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </form>
      </section>

      <DiscoverModal
        open={discoverOpen}
        onClose={() => setDiscoverOpen(false)}
        connectors={connectors.data ?? []}
      />
    </div>
  );
}
