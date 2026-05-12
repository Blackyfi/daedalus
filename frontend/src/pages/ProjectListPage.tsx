import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  GitStatusMap,
  Project,
  ProjectStatsMap,
  RunnerSnapshot,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";
import DiscoverModal from "../components/DiscoverModal";
import ProjectCard from "../components/ProjectCard";
import ProjectIdeaBox from "../components/ProjectIdeaBox";

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

  return (
    <div className="grid grid-cols-3 gap-6">
      <section className="col-span-2 panel">
        <header className="mb-3 flex items-center justify-between">
          <h2 className="text-sm uppercase tracking-wide text-muted">Projects</h2>
          <div className="flex items-center gap-3 text-xs text-muted">
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
            />
          ))}
        </div>
      </section>

      <aside className="col-span-1 space-y-6">
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

        <ProjectIdeaBox connectors={connectors.data ?? []} />
      </aside>

      <DiscoverModal
        open={discoverOpen}
        onClose={() => setDiscoverOpen(false)}
        connectors={connectors.data ?? []}
      />
    </div>
  );
}
