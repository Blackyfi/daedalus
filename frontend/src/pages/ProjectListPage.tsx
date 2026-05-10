import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  GitStatusMap,
  Project,
  ProjectStatsMap,
  RunnerSnapshot,
  SystemConfig,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";
import DiscoverModal from "../components/DiscoverModal";
import ProjectCard from "../components/ProjectCard";

// Slugify a project name into a filesystem-safe directory leaf:
// lowercase, ASCII alnum + `_.-`, collapse runs of dashes, trim leading/trailing.
function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}

function joinPath(root: string, leaf: string): string {
  if (!root) return leaf;
  if (!leaf) return root;
  return `${root.replace(/\/+$/, "")}/${leaf}`;
}

const EMPTY_FORM = {
  name: "",
  description: "",
  workspace_path: "",
  default_connector_id: "",
  init_git_repo: false,
};

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
  const systemConfig = useQuery<SystemConfig>({
    queryKey: ["system-config"],
    queryFn: () => api("/api/v1/system/config"),
    staleTime: Infinity,
  });
  const workspacesRoot = systemConfig.data?.workspaces_root ?? "";

  const activeByProject = new Map(
    (runners.data?.active ?? []).map((a) => [a.project_id, a]),
  );

  const [form, setForm] = useState(EMPTY_FORM);
  // True once the user has hand-edited the workspace path — locks the
  // auto-suggest so we don't overwrite a deliberate choice.
  const [pathTouched, setPathTouched] = useState(false);
  const [discoverOpen, setDiscoverOpen] = useState(false);

  // Auto-suggest `<workspaces_root>/<slug>` as the name changes, unless the
  // user has typed into the path field directly. Also runs once the config
  // query resolves so an empty path gets populated as soon as the root is known.
  useEffect(() => {
    if (pathTouched) return;
    if (!workspacesRoot) return;
    const slug = slugify(form.name);
    const suggested = slug ? joinPath(workspacesRoot, slug) : "";
    setForm((prev) =>
      prev.workspace_path === suggested
        ? prev
        : { ...prev, workspace_path: suggested },
    );
  }, [form.name, workspacesRoot, pathTouched]);

  const create = useMutation({
    mutationFn: (body: typeof form) =>
      apiJson("/api/v1/projects", {
        ...body,
        default_connector_id: body.default_connector_id || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setForm(EMPTY_FORM);
      setPathTouched(false);
      flash("Project created", "success");
    },
    onError: (err: any) => flash(err.message || "Project create failed", "error"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(form);
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-6">
      <section className="panel lg:col-span-2">
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

      <section className="panel">
        <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">New project</h2>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="label" htmlFor="np-name">Name</label>
            <input
              id="np-name"
              className="field"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="np-description">Description</label>
            <textarea
              id="np-description"
              className="field"
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div>
            <label className="label" htmlFor="np-workspace">Workspace path</label>
            <input
              id="np-workspace"
              className="field"
              value={form.workspace_path}
              onChange={(e) => {
                setPathTouched(true);
                setForm({ ...form, workspace_path: e.target.value });
              }}
              placeholder={
                workspacesRoot ? `${workspacesRoot}/my-repo` : "/workspaces/my-repo"
              }
              required
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
            <p className="mt-1 text-xs text-muted">
              Resolved:{" "}
              <span className="font-mono break-all text-text">
                {form.workspace_path || (workspacesRoot ? `${workspacesRoot}/…` : "—")}
              </span>
              {!pathTouched && workspacesRoot && (
                <span className="ml-1 italic">(auto from name)</span>
              )}
            </p>
          </div>
          <div>
            <label className="flex items-start gap-2 text-sm min-h-[44px] md:min-h-0">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={form.init_git_repo}
                onChange={(e) =>
                  setForm({ ...form, init_git_repo: e.target.checked })
                }
              />
              <span>
                Initialize empty git repository
                <span className="block text-xs text-muted">
                  Runs <code>git init</code> in the workspace path if it doesn't
                  already contain a repo.
                </span>
              </span>
            </label>
          </div>
          <div>
            <label className="label" htmlFor="np-connector">Default connector</label>
            <select
              id="np-connector"
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
