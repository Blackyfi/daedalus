import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AutoRunDefaults,
  AutoRunDefaultsPatch,
  Connector,
  TaskStatusValue,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";

interface FormState {
  enabled: boolean;
  max_fix_loops: number;
  daily_cap: number;
  hourly_cap: number;
  concurrency_cap: number;
  quiet_start: string;
  quiet_end: string;
  eligible_statuses: Set<TaskStatusValue>;
  allowed_connectors: Set<string>;
}

const ALL_TASK_STATUSES: TaskStatusValue[] = [
  "backlog",
  "ready",
  "in_progress",
  "verifying",
  "needs_fixes",
  "done",
  "cancelled",
];

const STATUS_LABEL: Record<string, string> = {
  backlog: "Backlog",
  ready: "Ready",
  needs_fixes: "Needs fixes",
  in_progress: "In progress",
  verifying: "Verifying",
  done: "Done",
  cancelled: "Cancelled",
};

function fromDefaults(d: AutoRunDefaults): FormState {
  return {
    enabled: d.enabled,
    max_fix_loops: d.max_fix_loops,
    daily_cap: d.daily_cap,
    hourly_cap: d.hourly_cap,
    concurrency_cap: d.concurrency_cap,
    quiet_start: d.quiet_hours_start == null ? "" : String(d.quiet_hours_start),
    quiet_end: d.quiet_hours_end == null ? "" : String(d.quiet_hours_end),
    eligible_statuses: new Set(d.eligible_statuses ?? []),
    allowed_connectors: new Set(d.allowed_connectors ?? []),
  };
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

export default function AutoRunDefaultsPanel() {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();

  // The defaults endpoint is read for anyone; PATCH is owner-only and
  // returns a 403 we surface inline. If the GET fails (e.g. user has no
  // role to see global config) we hide the section.
  const defaults = useQuery<AutoRunDefaults>({
    queryKey: ["autorun-defaults"],
    queryFn: () => api<AutoRunDefaults>("/api/v1/autorun/defaults"),
  });

  const connectors = useQuery<Connector[]>({
    queryKey: ["connectors"],
    queryFn: () => api<Connector[]>("/api/v1/connectors"),
  });

  const [form, setForm] = useState<FormState | null>(null);
  useEffect(() => {
    if (defaults.data) setForm(fromDefaults(defaults.data));
  }, [defaults.data]);

  const save = useMutation<AutoRunDefaults, Error, AutoRunDefaultsPatch>({
    mutationFn: (patch) =>
      apiJson<AutoRunDefaults>("/api/v1/autorun/defaults", patch, {
        method: "PATCH",
      }),
    onSuccess: (next) => {
      flash("Auto-run defaults saved", "success");
      qc.setQueryData(["autorun-defaults"], next);
      setForm(fromDefaults(next));
    },
    onError: (err) => flash(err.message || "Save failed", "error"),
  });

  const dirty = useMemo(() => {
    if (!form || !defaults.data) return false;
    const d = defaults.data;
    return (
      form.enabled !== d.enabled ||
      form.max_fix_loops !== d.max_fix_loops ||
      form.daily_cap !== d.daily_cap ||
      form.hourly_cap !== d.hourly_cap ||
      form.concurrency_cap !== d.concurrency_cap ||
      form.quiet_start !== (d.quiet_hours_start == null ? "" : String(d.quiet_hours_start)) ||
      form.quiet_end !== (d.quiet_hours_end == null ? "" : String(d.quiet_hours_end)) ||
      !setsEqual(form.eligible_statuses, new Set(d.eligible_statuses ?? [])) ||
      !setsEqual(form.allowed_connectors, new Set(d.allowed_connectors ?? []))
    );
  }, [form, defaults.data]);

  function toggleStatus(s: TaskStatusValue) {
    setForm((cur) => {
      if (!cur) return cur;
      const next = new Set(cur.eligible_statuses);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return { ...cur, eligible_statuses: next };
    });
  }

  function toggleConnector(id: string) {
    setForm((cur) => {
      if (!cur) return cur;
      const next = new Set(cur.allowed_connectors);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...cur, allowed_connectors: next };
    });
  }

  function onSave() {
    if (!form) return;
    const qs = form.quiet_start === "" ? null : Number(form.quiet_start);
    const qe = form.quiet_end === "" ? null : Number(form.quiet_end);
    if ((qs === null) !== (qe === null)) {
      flash("Quiet hours: set both start and end, or clear both", "error");
      return;
    }
    save.mutate({
      enabled: form.enabled,
      max_fix_loops: form.max_fix_loops,
      daily_cap: form.daily_cap,
      hourly_cap: form.hourly_cap,
      concurrency_cap: form.concurrency_cap,
      quiet_hours_start: qs,
      quiet_hours_end: qe,
      eligible_statuses: Array.from(form.eligible_statuses),
      allowed_connectors: Array.from(form.allowed_connectors),
    });
  }

  if (defaults.isLoading) {
    return (
      <section className="panel">
        <h3 className="text-xs uppercase tracking-wide text-muted">Auto-run defaults</h3>
        <p className="mt-2 text-xs text-muted">Loading…</p>
      </section>
    );
  }
  if (defaults.error || !defaults.data || !form) {
    return null;
  }

  const cxs = connectors.data ?? [];

  return (
    <section className="panel space-y-4" aria-labelledby="autorun-defaults-heading">
      <header className="space-y-1">
        <h3
          id="autorun-defaults-heading"
          className="text-xs uppercase tracking-wide text-muted"
        >
          Auto-run defaults (global)
        </h3>
        <p className="text-[11px] text-muted">
          Org-wide policy. Per-project Auto-run panels can override any of
          these fields; this is the fallback the system uses when a project
          hasn't set its own value, and the suggested starting point for
          new projects.
        </p>
      </header>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => setForm((s) => s && { ...s, enabled: e.target.checked })}
        />
        <span className="font-medium">Enable auto-run by default</span>
      </label>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-wide text-muted">
          Eligible task statuses
        </legend>
        <div className="flex flex-wrap gap-1.5">
          {ALL_TASK_STATUSES.map((s) => {
            const on = form.eligible_statuses.has(s);
            return (
              <label
                key={s}
                className={`inline-flex items-center gap-1 cursor-pointer rounded border px-2 py-0.5 text-[11px] ${
                  on
                    ? `status-pill status-${s} border-transparent`
                    : "border-border text-muted hover:text-fg"
                }`}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={on}
                  onChange={() => toggleStatus(s)}
                />
                {STATUS_LABEL[s] ?? s}
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-wide text-muted">
          Allowed connectors
        </legend>
        {cxs.length === 0 ? (
          <p className="text-[11px] text-muted">No connectors registered yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {cxs.map((c) => {
              const on = form.allowed_connectors.has(c.connector_id);
              return (
                <label
                  key={c.connector_id}
                  className={`inline-flex items-center gap-1 cursor-pointer rounded border px-2 py-0.5 text-[11px] ${
                    on
                      ? "border-accent bg-accent/10 text-fg"
                      : "border-border text-muted hover:text-fg"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={on}
                    onChange={() => toggleConnector(c.connector_id)}
                  />
                  {c.display_name}
                </label>
              );
            })}
          </div>
        )}
        <p className="text-[11px] text-muted">
          Empty list = all enabled connectors. Restrict to a subset for the
          global default.
        </p>
      </fieldset>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-max-loops">
            Max fix loops
          </label>
          <input
            id="ard-max-loops"
            type="number"
            min={0}
            max={20}
            className="field"
            value={form.max_fix_loops}
            onChange={(e) =>
              setForm((s) => s && { ...s, max_fix_loops: Number(e.target.value) })
            }
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-daily">
            Daily cap
          </label>
          <input
            id="ard-daily"
            type="number"
            min={0}
            max={500}
            className="field"
            value={form.daily_cap}
            onChange={(e) =>
              setForm((s) => s && { ...s, daily_cap: Number(e.target.value) })
            }
          />
          <p className="text-[11px] text-muted">0 = unlimited</p>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-hourly">
            Hourly cap
          </label>
          <input
            id="ard-hourly"
            type="number"
            min={0}
            max={500}
            className="field"
            value={form.hourly_cap}
            onChange={(e) =>
              setForm((s) => s && { ...s, hourly_cap: Number(e.target.value) })
            }
          />
          <p className="text-[11px] text-muted">Rolling 1-hour window. 0 = unlimited.</p>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-concurrency">
            Concurrency cap
          </label>
          <input
            id="ard-concurrency"
            type="number"
            min={0}
            max={64}
            className="field"
            value={form.concurrency_cap}
            onChange={(e) =>
              setForm((s) => s && { ...s, concurrency_cap: Number(e.target.value) })
            }
          />
          <p className="text-[11px] text-muted">Max simultaneous auto-runs.</p>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-quiet-start">
            Quiet start
          </label>
          <input
            id="ard-quiet-start"
            type="number"
            min={0}
            max={23}
            placeholder="—"
            className="field"
            value={form.quiet_start}
            onChange={(e) =>
              setForm((s) => s && { ...s, quiet_start: e.target.value })
            }
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted" htmlFor="ard-quiet-end">
            Quiet end
          </label>
          <input
            id="ard-quiet-end"
            type="number"
            min={0}
            max={23}
            placeholder="—"
            className="field"
            value={form.quiet_end}
            onChange={(e) =>
              setForm((s) => s && { ...s, quiet_end: e.target.value })
            }
          />
        </div>
      </div>

      <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between">
        <button
          className="btn"
          onClick={() => setForm(fromDefaults(defaults.data))}
          disabled={!dirty || save.isPending}
        >
          Reset
        </button>
        {!dirty && !save.isPending && (
          <span className="text-[11px] text-muted">no changes</span>
        )}
        <button
          className="btn btn-primary"
          onClick={onSave}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? "Saving…" : "Save defaults"}
        </button>
      </div>
    </section>
  );
}
