import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ANTHROPIC_MODELS, Connector, Project, apiJson } from "../api";
import { useApp } from "../store";

interface Props {
  project: Project;
  connectors: Connector[];
}

interface FormState {
  planning_model: string;
  task_model: string;
  verifier_model: string;
  argus_enabled: boolean;
  max_fix_loops: number;
  wall_clock_minutes_override: string;
  default_connector_id: string;
}

function fromProject(p: Project): FormState {
  return {
    planning_model: p.planning_model ?? "",
    task_model: p.task_model ?? "",
    verifier_model: p.verifier_model ?? "",
    argus_enabled: p.argus_enabled,
    max_fix_loops: p.max_fix_loops,
    wall_clock_minutes_override:
      p.wall_clock_minutes_override == null ? "" : String(p.wall_clock_minutes_override),
    default_connector_id: p.default_connector_id ?? "",
  };
}

export default function ProjectSettings({ project, connectors }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(() => fromProject(project));

  // Re-sync the form whenever the underlying project changes (e.g. after a save
  // round-trip or someone else edits it). Only resets when collapsed so we
  // don't yank values out from under the user mid-edit.
  useEffect(() => {
    if (!open) setForm(fromProject(project));
  }, [project, open]);

  const save = useMutation({
    mutationFn: () =>
      apiJson<Project>(
        `/api/v1/projects/${project.id}`,
        {
          planning_model: form.planning_model || null,
          task_model: form.task_model || null,
          verifier_model: form.verifier_model || null,
          argus_enabled: form.argus_enabled,
          max_fix_loops: form.max_fix_loops,
          wall_clock_minutes_override:
            form.wall_clock_minutes_override === ""
              ? null
              : Number(form.wall_clock_minutes_override),
          default_connector_id: form.default_connector_id || null,
        },
        { method: "PATCH" },
      ),
    onSuccess: () => {
      flash("Project settings saved", "success");
      qc.invalidateQueries({ queryKey: ["project", project.id] });
    },
    onError: (err: any) => flash(err.message || "Save failed", "error"),
  });

  const dirty =
    form.planning_model !== (project.planning_model ?? "") ||
    form.task_model !== (project.task_model ?? "") ||
    form.verifier_model !== (project.verifier_model ?? "") ||
    form.argus_enabled !== project.argus_enabled ||
    form.max_fix_loops !== project.max_fix_loops ||
    form.wall_clock_minutes_override !==
      (project.wall_clock_minutes_override == null
        ? ""
        : String(project.wall_clock_minutes_override)) ||
    form.default_connector_id !== (project.default_connector_id ?? "");

  return (
    <section className="panel">
      <header
        className="flex items-center justify-between cursor-pointer select-none"
        onClick={() => setOpen((o) => !o)}
      >
        <h2 className="text-sm font-semibold">Settings</h2>
        <span className="text-xs text-muted">{open ? "▾" : "▸"}</span>
      </header>

      {open && (
        <div className="mt-3 space-y-3 text-sm">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted" htmlFor="default-connector">
              Default connector
            </label>
            <select
              id="default-connector"
              className="field"
              value={form.default_connector_id}
              onChange={(e) =>
                setForm((s) => ({ ...s, default_connector_id: e.target.value }))
              }
            >
              <option value="">(none — pick per task)</option>
              {connectors.map((c) => (
                <option key={c.connector_id} value={c.connector_id}>
                  {c.display_name}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted">
              Used when a task is created without an explicit connector. Pick
              "Claude Code (live)" to stream the interactive TUI to the run
              panel and steer it from the browser.
            </p>
          </div>

          <ModelField
            label="Planning model"
            help="Used when Daedalus turns ideas into tasks."
            value={form.planning_model}
            onChange={(v) => setForm((s) => ({ ...s, planning_model: v }))}
          />
          <ModelField
            label="Task model"
            help="Injected as ANTHROPIC_MODEL when running task connectors."
            value={form.task_model}
            onChange={(v) => setForm((s) => ({ ...s, task_model: v }))}
          />
          <ModelField
            label="Verifier model"
            help="Used by Argus for verification. Cheaper/faster is fine."
            value={form.verifier_model}
            onChange={(v) => setForm((s) => ({ ...s, verifier_model: v }))}
          />

          <label className="flex min-h-[40px] cursor-pointer items-center gap-2 py-1 md:min-h-0">
            <input
              type="checkbox"
              className="h-4 w-4 cursor-pointer accent-accent md:h-3.5 md:w-3.5"
              checked={form.argus_enabled}
              onChange={(e) =>
                setForm((s) => ({ ...s, argus_enabled: e.target.checked }))
              }
            />
            <span>Argus verification enabled</span>
          </label>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted" htmlFor="max-fix-loops">
              Max fix loops
            </label>
            <input
              id="max-fix-loops"
              type="number"
              min={0}
              max={20}
              className="field"
              value={form.max_fix_loops}
              onChange={(e) =>
                setForm((s) => ({ ...s, max_fix_loops: Number(e.target.value) }))
              }
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted" htmlFor="wall-clock">
              Wall-clock cap (minutes)
            </label>
            <input
              id="wall-clock"
              type="number"
              min={1}
              max={1440}
              placeholder="(use connector default)"
              className="field"
              value={form.wall_clock_minutes_override}
              onChange={(e) =>
                setForm((s) => ({
                  ...s,
                  wall_clock_minutes_override: e.target.value,
                }))
              }
            />
          </div>

          <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between">
            <button
              className="btn w-full sm:w-auto"
              onClick={() => setForm(fromProject(project))}
              disabled={!dirty || save.isPending}
            >
              Reset
            </button>
            {!dirty && !save.isPending && (
              <span className="hidden text-[11px] text-muted sm:inline">
                no changes
              </span>
            )}
            <button
              className="btn btn-primary w-full sm:w-auto"
              onClick={() => save.mutate()}
              disabled={!dirty || save.isPending}
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function ModelField({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted">{label}</label>
      <select
        className="field"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">(use platform default)</option>
        {ANTHROPIC_MODELS.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} — {m.tier}
          </option>
        ))}
      </select>
      <p className="text-[11px] text-muted">{help}</p>
    </div>
  );
}
