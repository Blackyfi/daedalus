import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AutoRunConfigPatch,
  AutoRunStatus,
  Connector,
  Project,
  SubscriptionInfo,
  TaskStatusValue,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";

interface Props {
  project: Project;
  connectors: Connector[];
}

interface FormState {
  enabled: boolean;
  max_fix_loops: number;
  wall_clock_minutes_override: string;
  default_connector_id: string;
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

function fromStatus(s: AutoRunStatus): FormState {
  return {
    enabled: s.enabled,
    max_fix_loops: s.max_fix_loops,
    wall_clock_minutes_override:
      s.wall_clock_minutes_override == null ? "" : String(s.wall_clock_minutes_override),
    default_connector_id: s.default_connector_id ?? "",
    daily_cap: s.auto_run_daily_cap,
    hourly_cap: s.auto_run_hourly_cap ?? 0,
    concurrency_cap: s.auto_run_concurrency_cap ?? 0,
    quiet_start:
      s.auto_run_quiet_hours_start == null ? "" : String(s.auto_run_quiet_hours_start),
    quiet_end:
      s.auto_run_quiet_hours_end == null ? "" : String(s.auto_run_quiet_hours_end),
    eligible_statuses: new Set(s.auto_run_eligible_statuses ?? s.eligible_task_statuses ?? []),
    allowed_connectors: new Set(s.auto_run_allowed_connectors ?? []),
  };
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function fmtHour(h: number | null): string {
  if (h == null) return "—";
  return `${String(h).padStart(2, "0")}:00`;
}

function quotaPct(info: SubscriptionInfo | undefined): number | null {
  if (!info || info.kind !== "ok") return null;
  return Math.max(info.weekly_used_pct ?? 0, info.five_hour_used_pct ?? 0);
}

export default function AutoRunPanel({ project, connectors }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  // Pull derived state (eligible statuses, quiet-hour status, runs-today,
  // recent auto-runs) from the dedicated panel endpoint instead of hard-
  // coding it on the client. The endpoint is the source of truth so the
  // panel stays correct if the scheduler's eligibility rules change.
  const status = useQuery<AutoRunStatus>({
    queryKey: ["autorun", project.id],
    queryFn: () => api(`/api/v1/autorun/projects/${project.id}`),
    refetchInterval: open ? 15_000 : 60_000,
  });

  const subscription = useQuery<SubscriptionInfo>({
    queryKey: ["subscription"],
    queryFn: () => api("/api/v1/system/subscription"),
    staleTime: 30_000,
  });

  const [form, setForm] = useState<FormState | null>(null);
  useEffect(() => {
    if (!status.data) return;
    // Only reset the form when collapsed so we don't yank values out from
    // under the user mid-edit on a background refetch.
    if (!open || form === null) {
      setForm(fromStatus(status.data));
    }
  }, [status.data, open, form]);

  const save = useMutation<AutoRunStatus, Error, AutoRunConfigPatch>({
    mutationFn: (patch) =>
      apiJson<AutoRunStatus>(
        `/api/v1/autorun/projects/${project.id}`,
        patch,
        { method: "PATCH" },
      ),
    onSuccess: (next) => {
      flash("Auto-run settings saved", "success");
      qc.setQueryData(["autorun", project.id], next);
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      setForm(fromStatus(next));
    },
    onError: (err) => flash(err.message || "Save failed", "error"),
  });

  const dirty = useMemo(() => {
    if (!form || !status.data) return false;
    const s = status.data;
    return (
      form.enabled !== s.enabled ||
      form.max_fix_loops !== s.max_fix_loops ||
      form.wall_clock_minutes_override !==
        (s.wall_clock_minutes_override == null
          ? ""
          : String(s.wall_clock_minutes_override)) ||
      form.default_connector_id !== (s.default_connector_id ?? "") ||
      form.daily_cap !== s.auto_run_daily_cap ||
      form.hourly_cap !== (s.auto_run_hourly_cap ?? 0) ||
      form.concurrency_cap !== (s.auto_run_concurrency_cap ?? 0) ||
      form.quiet_start !==
        (s.auto_run_quiet_hours_start == null
          ? ""
          : String(s.auto_run_quiet_hours_start)) ||
      form.quiet_end !==
        (s.auto_run_quiet_hours_end == null ? "" : String(s.auto_run_quiet_hours_end)) ||
      !setsEqual(form.eligible_statuses, new Set(s.auto_run_eligible_statuses ?? [])) ||
      !setsEqual(form.allowed_connectors, new Set(s.auto_run_allowed_connectors ?? []))
    );
  }, [form, status.data]);

  function toggleStatus(taskStatus: TaskStatusValue) {
    setForm((s) => {
      if (!s) return s;
      const next = new Set(s.eligible_statuses);
      if (next.has(taskStatus)) next.delete(taskStatus);
      else next.add(taskStatus);
      return { ...s, eligible_statuses: next };
    });
  }

  function toggleConnector(id: string) {
    setForm((s) => {
      if (!s) return s;
      const next = new Set(s.allowed_connectors);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...s, allowed_connectors: next };
    });
  }

  function onSave() {
    if (!form) return;
    const quietStart = form.quiet_start === "" ? null : Number(form.quiet_start);
    const quietEnd = form.quiet_end === "" ? null : Number(form.quiet_end);
    if ((quietStart === null) !== (quietEnd === null)) {
      flash("Quiet hours: set both start and end, or clear both", "error");
      return;
    }
    save.mutate({
      auto_run_fix: form.enabled,
      max_fix_loops: form.max_fix_loops,
      wall_clock_minutes_override:
        form.wall_clock_minutes_override === ""
          ? null
          : Number(form.wall_clock_minutes_override),
      default_connector_id: form.default_connector_id || null,
      auto_run_quiet_hours_start: quietStart,
      auto_run_quiet_hours_end: quietEnd,
      auto_run_daily_cap: form.daily_cap,
      auto_run_hourly_cap: form.hourly_cap,
      auto_run_concurrency_cap: form.concurrency_cap,
      auto_run_eligible_statuses: Array.from(form.eligible_statuses),
      auto_run_allowed_connectors: Array.from(form.allowed_connectors),
    });
  }

  const usagePct = quotaPct(subscription.data);
  const quotaWarning = usagePct != null && usagePct >= 85;

  const data = status.data;
  const recent = data?.recent_runs ?? [];
  const autoTriggered = recent.filter((r) => r.auto_triggered);

  return (
    <section className="panel" aria-labelledby="autorun-heading">
      <header
        className="flex items-center justify-between cursor-pointer select-none gap-2"
        onClick={() => setOpen((o) => !o)}
        role="button"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <h2 id="autorun-heading" className="text-sm font-semibold truncate">
            Auto-run
          </h2>
          <StatusDot data={data} />
        </div>
        <span className="text-xs text-muted shrink-0">{open ? "▾" : "▸"}</span>
      </header>

      {!open && data && (
        <p className="mt-1 text-[11px] text-muted">
          {data.enabled ? "On" : "Off"}
          {" · "}
          {data.in_quiet_hours ? "quiet hours active" : "outside quiet hours"}
          {" · "}
          {data.daily_cap_remaining == null
            ? "unlimited daily"
            : `${data.daily_cap_remaining}/${data.auto_run_daily_cap} left today`}
        </p>
      )}

      {open && (
        <div className="mt-3 space-y-4 text-sm">
          {!data && <p className="text-muted">Loading auto-run status…</p>}
          {data && form && (
            <>
              <UsageIndicator
                subscription={subscription.data}
                data={data}
              />

              {data.in_quiet_hours && (
                <p className="rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning">
                  Quiet hours are active right now — auto-run is paused until
                  {" "}{fmtHour(data.auto_run_quiet_hours_end)}.
                </p>
              )}
              {quotaWarning && (
                <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1 text-[11px] text-danger">
                  Subscription quota is at {Math.round(usagePct!)}% — auto-run
                  may exhaust it. Consider lowering the daily cap.
                </p>
              )}

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) =>
                    setForm((s) => s && { ...s, enabled: e.target.checked })
                  }
                />
                <span className="font-medium">
                  Enable auto-run for fix-loops
                </span>
              </label>
              <p className="-mt-2 text-[11px] text-muted">
                When Argus reports a partial / fail verdict, automatically
                queue the generated fix task instead of waiting for a click.
              </p>

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
                <p className="text-[11px] text-muted">
                  Auto-run only queues tasks in the checked statuses. Other
                  tasks stay parked until a human moves them.
                </p>
              </fieldset>

              <fieldset className="space-y-2">
                <legend className="text-xs uppercase tracking-wide text-muted">
                  Allowed connectors
                </legend>
                {connectors.length === 0 ? (
                  <p className="text-[11px] text-muted">
                    No connectors registered yet.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {connectors.map((c) => {
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
                  Empty list = any enabled connector. Pick a subset to keep
                  auto-run on a known-good tool.
                </p>
              </fieldset>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-max-loops">
                    Max fix loops
                  </label>
                  <input
                    id="ar-max-loops"
                    type="number"
                    min={0}
                    max={20}
                    className="field"
                    value={form.max_fix_loops}
                    onChange={(e) =>
                      setForm(
                        (s) => s && { ...s, max_fix_loops: Number(e.target.value) },
                      )
                    }
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-wallclock">
                    Wall-clock cap (min)
                  </label>
                  <input
                    id="ar-wallclock"
                    type="number"
                    min={1}
                    max={1440}
                    placeholder="(connector default)"
                    className="field"
                    value={form.wall_clock_minutes_override}
                    onChange={(e) =>
                      setForm(
                        (s) =>
                          s && { ...s, wall_clock_minutes_override: e.target.value },
                      )
                    }
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-daily-cap">
                    Daily cap
                  </label>
                  <input
                    id="ar-daily-cap"
                    type="number"
                    min={0}
                    max={500}
                    className="field"
                    value={form.daily_cap}
                    onChange={(e) =>
                      setForm(
                        (s) => s && { ...s, daily_cap: Number(e.target.value) },
                      )
                    }
                  />
                  <p className="text-[11px] text-muted">0 = unlimited</p>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-hourly-cap">
                    Hourly window cap
                  </label>
                  <input
                    id="ar-hourly-cap"
                    type="number"
                    min={0}
                    max={500}
                    className="field"
                    value={form.hourly_cap}
                    onChange={(e) =>
                      setForm(
                        (s) => s && { ...s, hourly_cap: Number(e.target.value) },
                      )
                    }
                  />
                  <p className="text-[11px] text-muted">
                    Rolling 1-hour window. 0 = unlimited.
                  </p>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-concurrency-cap">
                    Concurrency cap
                  </label>
                  <input
                    id="ar-concurrency-cap"
                    type="number"
                    min={0}
                    max={64}
                    className="field"
                    value={form.concurrency_cap}
                    onChange={(e) =>
                      setForm(
                        (s) => s && { ...s, concurrency_cap: Number(e.target.value) },
                      )
                    }
                  />
                  <p className="text-[11px] text-muted">
                    Max simultaneous auto-runs. 0 = unlimited.
                  </p>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted" htmlFor="ar-connector">
                    Default connector
                  </label>
                  <select
                    id="ar-connector"
                    className="field"
                    value={form.default_connector_id}
                    onChange={(e) =>
                      setForm(
                        (s) => s && { ...s, default_connector_id: e.target.value },
                      )
                    }
                  >
                    <option value="">(none — pick per task)</option>
                    {connectors.map((c) => (
                      <option key={c.connector_id} value={c.connector_id}>
                        {c.display_name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <fieldset className="space-y-2 rounded border border-border p-3">
                <legend className="px-1 text-xs uppercase tracking-wide text-muted">
                  Quiet hours
                </legend>
                <p className="text-[11px] text-muted">
                  Inside this window the scheduler will not auto-queue fix
                  tasks. Manual runs are unaffected. Wrap-around (e.g. 22→6)
                  is supported. Leave blank to disable.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <label className="text-xs text-muted" htmlFor="ar-quiet-start">
                      Start (hour 0–23)
                    </label>
                    <input
                      id="ar-quiet-start"
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
                    <label className="text-xs text-muted" htmlFor="ar-quiet-end">
                      End (hour 0–23)
                    </label>
                    <input
                      id="ar-quiet-end"
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
              </fieldset>

              <RecentRuns runs={recent} autoCount={autoTriggered.length} />

              <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between">
                <button
                  className="btn"
                  onClick={() => setForm(fromStatus(data))}
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
                  {save.isPending ? "Saving…" : "Save"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function StatusDot({ data }: { data: AutoRunStatus | undefined }) {
  if (!data) {
    return <span className="inline-block h-2 w-2 rounded-full bg-muted" />;
  }
  const cls = !data.enabled
    ? "bg-muted"
    : data.in_quiet_hours
      ? "bg-warning"
      : data.daily_cap_remaining === 0
        ? "bg-danger"
        : "bg-accent";
  const title = !data.enabled
    ? "Auto-run is disabled"
    : data.in_quiet_hours
      ? "Auto-run is in quiet hours"
      : data.daily_cap_remaining === 0
        ? "Daily cap exhausted"
        : "Auto-run is active";
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${cls}`}
      title={title}
      aria-label={title}
    />
  );
}

function UsageBar({
  label,
  used,
  cap,
  remaining,
  hint,
}: {
  label: string;
  used: number;
  cap: number;
  remaining: number | null;
  hint?: string;
}) {
  const pct =
    cap === 0 ? 100 : Math.min(100, Math.round((used / Math.max(cap, 1)) * 100));
  const barCls =
    cap === 0
      ? "h-full bg-accent/40"
      : remaining === 0
        ? "h-full bg-danger"
        : pct >= 80
          ? "h-full bg-warning"
          : "h-full bg-accent";
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-1 text-[11px]">
        <span className="text-muted">{label}</span>
        <span className="font-mono">
          {used}
          {cap === 0 ? " (no cap)" : ` / ${cap}`}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-border">
        <div className={barCls} style={{ width: `${pct}%` }} />
      </div>
      {hint && <p className="mt-0.5 text-[10px] text-muted">{hint}</p>}
    </div>
  );
}

function UsageIndicator({
  subscription,
  data,
}: {
  subscription: SubscriptionInfo | undefined;
  data: AutoRunStatus;
}) {
  const pct = quotaPct(subscription);
  return (
    <div className="rounded border border-border bg-panel2 p-3 space-y-2">
      <UsageBar
        label="Auto-runs today"
        used={data.runs_today}
        cap={data.auto_run_daily_cap}
        remaining={data.daily_cap_remaining}
      />
      <UsageBar
        label="Last hour"
        used={data.runs_last_hour}
        cap={data.auto_run_hourly_cap}
        remaining={data.hourly_cap_remaining}
      />
      <UsageBar
        label="Active auto-runs"
        used={data.active_auto_runs}
        cap={data.auto_run_concurrency_cap}
        remaining={data.concurrency_remaining}
        hint="Counts queued + running auto-launched runs against the concurrency cap."
      />
      {pct != null && (
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[11px] text-muted border-t border-border">
          <span>Subscription quota</span>
          <span className="font-mono">{Math.round(pct)}%</span>
        </div>
      )}
    </div>
  );
}

function RecentRuns({
  runs,
  autoCount,
}: {
  runs: AutoRunStatus["recent_runs"];
  autoCount: number;
}) {
  if (runs.length === 0) {
    return (
      <fieldset className="rounded border border-border p-3">
        <legend className="px-1 text-xs uppercase tracking-wide text-muted">
          Recent runs
        </legend>
        <p className="text-[11px] text-muted">No runs yet for this project.</p>
      </fieldset>
    );
  }
  return (
    <fieldset className="rounded border border-border p-3">
      <legend className="px-1 text-xs uppercase tracking-wide text-muted">
        Recent runs ({autoCount} auto-launched)
      </legend>
      <ul className="-mx-1 max-h-48 overflow-auto text-[11px]">
        {runs.map((r) => (
          <li
            key={r.id}
            className="flex items-center justify-between gap-2 px-1 py-1 border-b border-border/40 last:border-b-0"
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <span
                className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                  r.auto_triggered ? "bg-accent" : "bg-muted"
                }`}
                title={r.auto_triggered ? "Auto-run" : "Manual run"}
              />
              <span className="truncate" title={r.task_title ?? "(no task)"}>
                {r.task_title ?? `(${r.kind})`}
              </span>
            </span>
            <span
              className={`status-pill status-${r.state}`}
              title={r.created_at}
            >
              {r.state}
            </span>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
