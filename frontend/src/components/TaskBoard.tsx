import { FormEvent, useEffect, useRef, useState } from "react";
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

  // Mobile column navigator: tracks which status column is currently
  // centered in the swipe lane (< sm). Above sm the lane becomes a
  // multi-column grid and this state is no longer reflected in the UI,
  // but we still keep it in sync so re-narrowing the viewport restores
  // the user's last selection.
  const [activeColumn, setActiveColumn] = useState<Task["status"]>(
    COLUMNS[0].key,
  );
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const columnRefs = useRef<Record<string, HTMLDivElement | null>>({});

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

  function showColumn(key: Task["status"]) {
    setActiveColumn(key);
    const el = columnRefs.current[key];
    const scroller = scrollerRef.current;
    if (el && scroller) {
      scroller.scrollTo({
        left: el.offsetLeft - scroller.offsetLeft,
        behavior: "smooth",
      });
    }
  }

  // Keep the segmented control in sync with horizontal swipes. Only
  // meaningful below `sm` where the lane is a snap-x scroller; at wider
  // widths the children are laid out by `grid` and `scrollLeft` stays 0.
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const center = scroller.scrollLeft + scroller.clientWidth / 2;
        let closestKey: Task["status"] | null = null;
        let closestDist = Infinity;
        for (const col of COLUMNS) {
          const el = columnRefs.current[col.key];
          if (!el) continue;
          const elCenter =
            el.offsetLeft - scroller.offsetLeft + el.offsetWidth / 2;
          const d = Math.abs(elCenter - center);
          if (d < closestDist) {
            closestDist = d;
            closestKey = col.key;
          }
        }
        if (closestKey) {
          setActiveColumn((prev) => (prev === closestKey ? prev : closestKey!));
        }
      });
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <section className="panel">
      <header className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm uppercase tracking-wide text-muted">Tasks</h2>
        <button
          className="btn min-h-[40px] md:min-h-0"
          onClick={() => setShowForm((v) => !v)}
        >
          {showForm ? "Cancel" : "+ New task"}
        </button>
      </header>

      {showForm && (
        <form
          onSubmit={submit}
          className="mb-4 grid grid-cols-1 gap-3 panel sm:grid-cols-2"
        >
          <div className="sm:col-span-2">
            <label className="label">Title</label>
            <input
              className="field"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              required
            />
          </div>
          <div className="sm:col-span-2">
            <label className="label">Description</label>
            <textarea
              className="field"
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
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
          <div className="sm:col-span-2">
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
          <div className="sm:col-span-2 flex justify-end">
            <button
              className="btn btn-primary w-full justify-center min-h-[44px] sm:w-auto sm:min-h-0"
              disabled={create.isPending}
            >
              {create.isPending ? "Saving…" : "Create"}
            </button>
          </div>
        </form>
      )}

      {/* Mobile column picker: visible only below `sm`. Tapping a tab
          scrolls the swipe lane to that column; horizontal swipes
          update the highlighted tab via the scroll listener above. */}
      <div
        role="tablist"
        aria-label="Task status"
        className="mb-2 -mx-1 flex gap-1 overflow-x-auto px-1 pb-1 sm:hidden"
      >
        {COLUMNS.map((col) => {
          const count = tasks.filter((t) => t.status === col.key).length;
          const active = activeColumn === col.key;
          return (
            <button
              key={col.key}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => showColumn(col.key)}
              className={
                "flex shrink-0 items-center gap-1.5 rounded border px-3 py-2 text-xs uppercase tracking-wide min-h-[40px] " +
                (active
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border bg-panel2 text-muted")
              }
            >
              <span>{col.label}</span>
              <span
                className={
                  "rounded px-1 text-[10px] " +
                  (active ? "bg-accent/20" : "bg-panel")
                }
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      <div
        ref={scrollerRef}
        className="-mx-1 flex snap-x snap-mandatory gap-2 overflow-x-auto px-1 pb-1 sm:mx-0 sm:grid sm:snap-none sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0 lg:grid-cols-6"
        style={{ scrollbarWidth: "none" }}
      >
        {COLUMNS.map((col) => {
          const items = tasks.filter((t) => t.status === col.key);
          return (
            <div
              key={col.key}
              ref={(el) => {
                columnRefs.current[col.key] = el;
              }}
              data-status={col.key}
              className="w-full shrink-0 snap-start rounded border border-border bg-panel2 p-2 sm:w-auto sm:shrink"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm uppercase tracking-wide text-muted sm:text-xs">
                  {col.label}
                </span>
                <span className="text-xs text-muted sm:text-[10px]">
                  {items.length}
                </span>
              </div>
              {/* Bound the column height so a project with hundreds of
                  done tasks doesn't drown the page; scroll vertically
                  inside the column instead of pushing siblings below the
                  fold. */}
              <div className="space-y-1 max-h-[60vh] overflow-y-auto pr-1 sm:max-h-[55vh]">
                {items.map((t) => (
                  <article
                    key={t.id}
                    className="rounded bg-panel border border-border p-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold leading-tight sm:text-xs">
                        {t.title}
                      </h3>
                      <span className="text-xs text-muted sm:text-[10px]">
                        {t.priority}
                      </span>
                    </div>
                    {t.tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {t.tags.map((tag) => (
                          <span key={tag} className="tag mr-0">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {(t.status === "backlog" ||
                      t.status === "ready" ||
                      t.status === "needs_fixes") && (
                      <button
                        onClick={() => onRunTask(t.id)}
                        disabled={!!runDisabledReason}
                        title={runDisabledReason ?? undefined}
                        className="btn btn-primary mt-2 w-full justify-center text-xs min-h-[44px] md:text-[10px] md:min-h-0"
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
