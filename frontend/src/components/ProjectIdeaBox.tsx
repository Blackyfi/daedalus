import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  Project,
  ProjectIdea,
  ProjectIdeaPromoteIn,
  ProjectIdeaStatus,
  api,
  apiJson,
  updateProjectIdea,
} from "../api";
import { useApp } from "../store";
import HelpTooltip from "./HelpTooltip";

interface Props {
  connectors: Connector[];
}

const HELP =
  "Project ideas are seeds for *new projects* — when you promote one, " +
  "Daedalus creates a real Project row (and optionally git-inits the " +
  "workspace). The Idea Box inside an existing project is for task " +
  "ideas — those get planned into individual tasks instead.";

const STATUS_LABEL: Record<ProjectIdeaStatus, string> = {
  new: "new",
  promoted: "promoted",
  archived: "archived",
};

/**
 * Derive a sensible workspace path slug from a free-form project name.
 * Lower-cases, swaps any run of non-alphanumerics for a single dash, and
 * trims leading/trailing dashes. Used by the new-project promote modal so
 * the user doesn't have to retype `/workspaces/<slug>` by hand.
 */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export default function ProjectIdeaBox({ connectors }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [tags, setTags] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [promoteTarget, setPromoteTarget] = useState<ProjectIdea | null>(null);

  const ideas = useQuery<ProjectIdea[]>({
    queryKey: ["project-ideas"],
    queryFn: () => api("/api/v1/project-ideas"),
  });

  const create = useMutation({
    mutationFn: (body: { text: string; tags: string[] }) =>
      apiJson("/api/v1/project-ideas", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project-ideas"] });
      setText("");
      setTags("");
    },
    onError: (err: any) =>
      flash(err.message || "Project idea create failed", "error"),
  });

  const update = useMutation({
    mutationFn: (vars: {
      id: string;
      patch: { text?: string; tags?: string[]; status?: ProjectIdeaStatus };
    }) => updateProjectIdea(vars.id, vars.patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-ideas"] }),
    onError: (err: any) =>
      flash(err.message || "Project idea update failed", "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/project-ideas/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-ideas"] }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({
      text: text.trim(),
      tags: tags
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    });
  }

  function startEdit(idea: ProjectIdea) {
    setEditingId(idea.id);
    setEditText(idea.text);
  }

  function commitEdit(idea: ProjectIdea) {
    const next = editText.trim();
    if (!next || next === idea.text) {
      setEditingId(null);
      return;
    }
    update.mutate(
      { id: idea.id, patch: { text: next } },
      { onSuccess: () => setEditingId(null) },
    );
  }

  const visible = useMemo(() => {
    const rows = ideas.data ?? [];
    return showArchived ? rows : rows.filter((r) => r.status !== "archived");
  }, [ideas.data, showArchived]);

  const archivedCount = (ideas.data ?? []).filter(
    (i) => i.status === "archived",
  ).length;

  return (
    <section className="panel">
      <header className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm uppercase tracking-wide text-muted">
            Project Idea Box
          </h2>
          <HelpTooltip text={HELP} label="What's a project idea?" />
        </div>
        {archivedCount > 0 && (
          <button
            type="button"
            className="text-[11px] text-muted hover:text-text"
            onClick={() => setShowArchived((v) => !v)}
          >
            {showArchived ? "hide archived" : `show archived (${archivedCount})`}
          </button>
        )}
      </header>

      <form onSubmit={submit} className="space-y-2">
        <textarea
          className="field"
          rows={3}
          placeholder="One project idea per box. e.g. 'CLI for indexing markdown notes'."
          value={text}
          onChange={(e) => setText(e.target.value)}
          required
        />
        <input
          className="field"
          placeholder="tags, comma, separated"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
        <button
          className="btn btn-primary w-full min-h-[44px] sm:min-h-0"
          disabled={create.isPending}
        >
          {create.isPending ? "Adding…" : "Add project idea"}
        </button>
      </form>

      <div className="mt-4 space-y-2">
        {ideas.isLoading && (
          <p className="text-xs text-muted">Loading project ideas…</p>
        )}
        {!ideas.isLoading && visible.length === 0 && (
          <p className="text-xs text-muted">No project ideas yet.</p>
        )}
        {visible.map((idea) => {
          const isEditing = editingId === idea.id;
          const isPromoted = idea.status === "promoted";
          const isArchived = idea.status === "archived";
          return (
            <article
              key={idea.id}
              className={`rounded border border-border bg-panel2 p-2 ${
                isArchived ? "opacity-60" : ""
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="mb-1 flex flex-wrap items-center gap-1">
                    <span className={`status-pill status-${idea.status}`}>
                      {STATUS_LABEL[idea.status]}
                    </span>
                    {idea.tags.map((t) => (
                      <span key={t} className="tag">
                        {t}
                      </span>
                    ))}
                  </div>
                  {isEditing ? (
                    <textarea
                      autoFocus
                      className="field"
                      rows={3}
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onBlur={() => commitEdit(idea)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") {
                          setEditingId(null);
                        } else if (
                          e.key === "Enter" &&
                          (e.metaKey || e.ctrlKey)
                        ) {
                          e.preventDefault();
                          commitEdit(idea);
                        }
                      }}
                    />
                  ) : (
                    <div
                      className={`text-xs whitespace-pre-wrap break-words ${
                        isPromoted || isArchived ? "" : "cursor-text"
                      }`}
                      onClick={() => {
                        if (!isPromoted && !isArchived) startEdit(idea);
                      }}
                      title={
                        isPromoted
                          ? "Promoted — no longer editable"
                          : isArchived
                            ? "Archived — restore to edit"
                            : "Click to edit"
                      }
                    >
                      {idea.text}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1 sm:flex-nowrap">
                  {!isPromoted && !isArchived && !isEditing && (
                    <button
                      type="button"
                      className="btn btn-primary text-[10px]"
                      onClick={() => setPromoteTarget(idea)}
                      title="Promote to a real project"
                    >
                      Promote
                    </button>
                  )}
                  {!isPromoted && (
                    <button
                      type="button"
                      className="btn text-[10px]"
                      onClick={() =>
                        update.mutate({
                          id: idea.id,
                          patch: {
                            status: isArchived ? "new" : "archived",
                          },
                        })
                      }
                      title={isArchived ? "Restore" : "Archive"}
                    >
                      {isArchived ? "Restore" : "Archive"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn text-[10px]"
                    onClick={() => {
                      if (
                        window.confirm(
                          "Delete this project idea? This cannot be undone.",
                        )
                      ) {
                        remove.mutate(idea.id);
                      }
                    }}
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
              </div>
            </article>
          );
        })}
      </div>

      {promoteTarget && (
        <PromoteModal
          idea={promoteTarget}
          connectors={connectors}
          onClose={() => setPromoteTarget(null)}
          onPromoted={() => {
            qc.invalidateQueries({ queryKey: ["project-ideas"] });
            qc.invalidateQueries({ queryKey: ["projects"] });
            flash("Project created from idea", "success");
            setPromoteTarget(null);
          }}
        />
      )}
    </section>
  );
}

interface PromoteModalProps {
  idea: ProjectIdea;
  connectors: Connector[];
  onClose: () => void;
  onPromoted: () => void;
}

function PromoteModal({
  idea,
  connectors,
  onClose,
  onPromoted,
}: PromoteModalProps) {
  const flash = useApp((s) => s.flash);

  // Pre-fill name from the idea's first non-empty line so the user has
  // something to react to instead of an empty form.
  const firstLine = (idea.text.split("\n")[0] || "").trim().slice(0, 160);

  const [form, setForm] = useState({
    name: firstLine,
    description: idea.text,
    workspace_path: "",
    git_default_branch: "main",
    default_connector_id: "",
    init_git: true,
  });
  // Has the user manually edited the workspace_path? If not, keep
  // re-deriving it from the name so the slug stays in sync.
  const [pathDirty, setPathDirty] = useState(false);

  useEffect(() => {
    if (pathDirty) return;
    const slug = slugify(form.name);
    setForm((f) => ({
      ...f,
      workspace_path: slug ? `/workspaces/${slug}` : "",
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.name]);

  const promote = useMutation<Project, Error, ProjectIdeaPromoteIn>({
    mutationFn: (body) =>
      apiJson<Project>(`/api/v1/project-ideas/${idea.id}/promote`, body),
    onSuccess: onPromoted,
    onError: (err) => flash(err.message || "Promotion failed", "error"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.workspace_path.trim()) {
      flash("Name and workspace path are required", "error");
      return;
    }
    promote.mutate({
      name: form.name.trim(),
      description: form.description || null,
      workspace_path: form.workspace_path.trim(),
      git_default_branch: form.git_default_branch || "main",
      default_connector_id: form.default_connector_id || null,
      init_git: form.init_git,
    });
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
      <div className="panel w-full max-w-md max-h-[90vh] overflow-y-auto">
        <header className="mb-3 flex items-center justify-between">
          <h3 className="text-sm uppercase tracking-wide text-muted">
            Promote to project
          </h3>
          <button className="btn text-[10px]" onClick={onClose}>
            close
          </button>
        </header>

        <form onSubmit={submit} className="space-y-3 text-sm">
          <div>
            <label className="label">Name</label>
            <input
              className="field"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              autoFocus
            />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea
              className="field"
              rows={3}
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
            />
          </div>
          <div>
            <label className="label flex items-center gap-2">
              Workspace path
              <HelpTooltip
                text="Where Daedalus will run this project. Suggested from the name; edit if you have an existing folder. Must live inside the configured workspaces root."
                label="Workspace path help"
              />
            </label>
            <input
              className="field font-mono"
              value={form.workspace_path}
              onChange={(e) => {
                setPathDirty(true);
                setForm({ ...form, workspace_path: e.target.value });
              }}
              placeholder="/workspaces/my-repo"
              required
            />
          </div>
          <div>
            <label className="label">Default branch</label>
            <input
              className="field"
              value={form.git_default_branch}
              onChange={(e) =>
                setForm({ ...form, git_default_branch: e.target.value })
              }
              placeholder="main"
            />
          </div>
          <div>
            <label className="label">Default connector</label>
            <select
              className="field"
              value={form.default_connector_id}
              onChange={(e) =>
                setForm({ ...form, default_connector_id: e.target.value })
              }
            >
              <option value="">(none)</option>
              {connectors.map((c) => (
                <option key={c.connector_id} value={c.connector_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={form.init_git}
              onChange={(e) =>
                setForm({ ...form, init_git: e.target.checked })
              }
            />
            <span>
              <code className="font-mono">git init</code> the workspace if it
              isn't already a repo
            </span>
          </label>

          <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={promote.isPending}
            >
              cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={promote.isPending}
            >
              {promote.isPending ? "Promoting…" : "Create project"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
