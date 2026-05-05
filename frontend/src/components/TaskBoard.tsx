import { FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Connector, Task, apiJson } from "../api";
import { useApp } from "../store";

interface Props {
  tasks: Task[];
  onRunTask: (id: string) => void;
  connectors: Connector[];
  projectId: string;
  /** When true (workspace is behind upstream), the per-task Run button is
   * disabled and shows a tooltip pointing the user at the banner. */
  runDisabledReason?: string | null;
}

const COLUMNS: { key: Task["status"]; label: string }[] = [
  { key: "backlog", label: "Backlog" },
  { key: "ready", label: "Ready" },
  { key: "in_progress", label: "Running" },
  { key: "verifying", label: "Verifying" },
  { key: "needs_fixes", label: "Needs fixes" },
  { key: "done", label: "Done" },
];

export default function TaskBoard({
  tasks,
  onRunTask,
  connectors,
  projectId,
  runDisabledReason,
}: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    title: "",
    description: "",
    acceptance_criteria: "",
    priority: "P2",
    connector_id: "",
    profile: "confirm",
  });

  const create = useMutation({
    mutationFn: (body: typeof form) =>
      apiJson(`/api/v1/projects/${projectId}/tasks`, {
        ...body,
        connector_id: body.connector_id || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      setForm({
        title: "",
        description: "",
        acceptance_criteria: "",
        priority: "P2",
        connector_id: "",
        profile: "confirm",
      });
      setShowForm(false);
      flash("Task created", "success");
    },
    onError: (err: any) => flash(err.message || "Task create failed", "error"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(form);
  }

  return (
    <section className="panel">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-sm uppercase tracking-wide text-muted">Tasks</h2>
        <button className="btn" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "+ New task"}
        </button>
      </header>

      {showForm && (
        <form onSubmit={submit} className="mb-4 grid grid-cols-2 gap-3 panel">
          <div className="col-span-2">
            <label className="label">Title</label>
            <input
              className="field"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              required
            />
          </div>
          <div className="col-span-2">
            <label className="label">Description</label>
            <textarea
              className="field"
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="col-span-2">
            <label className="label">Acceptance criteria</label>
            <textarea
              className="field"
              rows={2}
              value={form.acceptance_criteria}
              onChange={(e) =>
                setForm({ ...form, acceptance_criteria: e.target.value })
              }
            />
          </div>
          <div>
            <label className="label">Priority</label>
            <select
              className="field"
              value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}
            >
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
              <option>P3</option>
            </select>
          </div>
          <div>
            <label className="label">Profile</label>
            <select
              className="field"
              value={form.profile}
              onChange={(e) => setForm({ ...form, profile: e.target.value })}
            >
              <option>confirm</option>
              <option>yolo</option>
            </select>
          </div>
          <div className="col-span-2">
            <label className="label">Connector</label>
            <select
              className="field"
              value={form.connector_id}
              onChange={(e) => setForm({ ...form, connector_id: e.target.value })}
            >
              <option value="">(use project default)</option>
              {connectors.map((c) => (
                <option key={c.connector_id} value={c.connector_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="col-span-2 flex justify-end">
            <button className="btn btn-primary" disabled={create.isPending}>
              {create.isPending ? "Saving…" : "Create"}
            </button>
          </div>
        </form>
      )}

      <div className="grid grid-cols-6 gap-2">
        {COLUMNS.map((col) => {
          const items = tasks.filter((t) => t.status === col.key);
          return (
            <div key={col.key} className="rounded border border-border bg-panel2 p-2">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-muted">
                  {col.label}
                </span>
                <span className="text-[10px] text-muted">{items.length}</span>
              </div>
              <div className="space-y-1">
                {items.map((t) => (
                  <article key={t.id} className="rounded bg-panel border border-border p-2">
                    <div className="flex items-start justify-between gap-1">
                      <h3 className="text-xs font-semibold leading-tight">{t.title}</h3>
                      <span className="text-[10px] text-muted">{t.priority}</span>
                    </div>
                    {t.tags.length > 0 && (
                      <div className="mt-1">
                        {t.tags.map((tag) => (
                          <span key={tag} className="tag">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {(t.status === "backlog" || t.status === "ready" || t.status === "needs_fixes") && (
                      <button
                        onClick={() => onRunTask(t.id)}
                        disabled={!!runDisabledReason}
                        title={runDisabledReason ?? undefined}
                        className="btn btn-primary mt-2 text-[10px] w-full justify-center"
                      >
                        {runDisabledReason ? "Run (blocked)" : "▶ Run"}
                      </button>
                    )}
                  </article>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
