import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AutoRunConfigPatch,
  AutoRunStatus,
  Connector,
  Project,
  SubscriptionInfo,
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
  quiet_start: string;
  quiet_end: string;
}

function fromStatus(s: AutoRunStatus): FormState {
  return {
    enabled: s.enabled,
    max_fix_loops: s.max_fix_loops,
    wall_clock_minutes_override:
      s.wall_clock_minutes_override == null ? "" : String(s.wall_clock_minutes_override),
    default_connector_id: s.default_connector_id ?? "",
    daily_cap: s.auto_run_daily_cap,
    quiet_start:
      s.auto_run_quiet_hours_start == null ? "" : String(s.auto_run_quiet_hours_start),
    quiet_end:
      s.auto_run_quiet_hours_end == null ? "" : String(s.auto_run_quiet_hours_end),
  };
}

const STATUS_LABEL: Record<string, string> = {
  backlog: "Backlog",
  ready: "Ready",
  needs_fixes: "Needs fixes",
  in_progress: "In progress",
  verifying: "Verifying",
  done: "Done",
  cancelled: "Cancelled",
};

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
      form.quiet_start !==
        (s.auto_run_quiet_hours_start == null
          ? ""
          : String(s.auto_run_quiet_hours_start)) ||
      form.quiet_end !==
        (s.auto_run_quiet_hours_end == null ? "" : String(s.auto_run_quiet_hours_end))
    );
  }, [form, status.data]);

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
                runsToday={data.runs_today}
                cap={data.auto_run_daily_cap}
                remaining={data.daily_cap_remaining}
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
                <div className="flex flex-wrap gap-1">
                  {data.eligible_task_statuses.map((s) => (
                    <span key={s} className={`status-pill status-${s}`}>
                      {STATUS_LABEL[s] ?? s}
                    </span>
                  ))}
                </div>
                <p className="text-[11px] text-muted">
                  Auto-run only queues tasks in these statuses. Other tasks
                  stay parked until a human moves them.
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
                    Daily auto-run cap
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
                <div className="grid grid-cols-2 gap-3">
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

function UsageIndicator({
  subscription,
  runsToday,
  cap,
  remaining,
}: {
  subscription: SubscriptionInfo | undefined;
  runsToday: number;
  cap: number;
  remaining: number | null;
}) {
  const pct = quotaPct(subscription);
  return (
    <div className="rounded border border-border bg-panel2 p-3 text-[11px]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted">Auto-runs today</span>
        <span className="font-mono">
          {runsToday}
          {cap === 0 ? " (no cap)" : ` / ${cap}`}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-border">
        <div
          className={
            cap === 0
              ? "h-full bg-accent/60"
              : remaining === 0
                ? "h-full bg-danger"
                : "h-full bg-accent"
          }
          style={{
            width:
              cap === 0
                ? "100%"
                : `${Math.min(100, Math.round((runsToday / cap) * 100))}%`,
          }}
        />
      </div>
      {pct != null && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-muted">
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
        Recent runs ({autoCount} auto)
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
