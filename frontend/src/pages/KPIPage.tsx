import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  AreaChart,
} from "recharts";
import { Project, api } from "../api";

type StatusKey =
  | "backlog"
  | "ready"
  | "in_progress"
  | "verifying"
  | "needs_fixes"
  | "done"
  | "cancelled";

interface TimeseriesResponse {
  statuses: StatusKey[];
  points: ({ date: string } & Record<StatusKey, number>)[];
}

// Match the rest of the app's palette + signal intent. Reds for blocked,
// green for done, neutral greys for queued/in-flight states.
const STATUS_COLORS: Record<StatusKey, string> = {
  backlog: "#8b949e",
  ready: "#58a6ff",
  in_progress: "#d29922",
  verifying: "#a371f7",
  needs_fixes: "#f85149",
  done: "#7ee787",
  cancelled: "#484f58",
};

const STATUS_LABELS: Record<StatusKey, string> = {
  backlog: "Backlog",
  ready: "Ready",
  in_progress: "In progress",
  verifying: "Verifying",
  needs_fixes: "Needs fixes",
  done: "Done",
  cancelled: "Cancelled",
};

const RANGE_OPTIONS = [
  { days: 7, label: "7d" },
  { days: 14, label: "14d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
];

export default function KPIPage() {
  const [projectId, setProjectId] = useState<string>("");
  const [days, setDays] = useState<number>(30);

  const projects = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: () => api("/api/v1/projects"),
  });

  // Default to the first project once the list loads.
  useEffect(() => {
    if (!projectId && projects.data && projects.data.length > 0) {
      setProjectId(projects.data[0].id);
    }
  }, [projectId, projects.data]);

  const series = useQuery<TimeseriesResponse>({
    queryKey: ["kpi-task-status", projectId, days],
    queryFn: () =>
      api(
        `/api/v1/kpis/projects/${projectId}/task-status-timeseries?days=${days}`,
      ),
    enabled: !!projectId,
    refetchInterval: 30_000,
  });

  const chartData = useMemo(() => {
    if (!series.data) return [];
    return series.data.points.map((p) => ({
      ...p,
      // Short label for the axis. e.g. "May 12".
      _label: new Date(p.date + "T00:00:00Z").toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      }),
    }));
  }, [series.data]);

  const totals = useMemo(() => {
    if (!series.data || series.data.points.length === 0) return null;
    const last = series.data.points[series.data.points.length - 1];
    const acc: Record<string, number> = {};
    for (const s of series.data.statuses) {
      acc[s] = (last as any)[s] ?? 0;
    }
    acc.total = Object.values(acc).reduce((a, b) => a + b, 0);
    return acc;
  }, [series.data]);

  return (
    <div className="space-y-4">
      <section className="panel">
        <header className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm uppercase tracking-wide text-muted">
            Task status over time
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs uppercase tracking-wide text-muted">
              Project
            </label>
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="rounded border border-border bg-panel2 px-2 py-1 text-sm"
            >
              {projects.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <div className="ml-2 flex items-center gap-1 text-xs">
              {RANGE_OPTIONS.map((r) => (
                <button
                  key={r.days}
                  onClick={() => setDays(r.days)}
                  className={`rounded px-2 py-1 ${
                    days === r.days
                      ? "bg-accent/10 text-accent"
                      : "text-muted hover:text-text"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
        </header>

        {projects.isLoading && (
          <p className="text-xs text-muted">Loading projects…</p>
        )}
        {projects.data && projects.data.length === 0 && (
          <p className="text-xs text-muted">
            No projects yet — create one from the Projects page.
          </p>
        )}
        {series.isError && (
          <p className="text-xs text-danger">
            {(series.error as Error).message}
          </p>
        )}

        {projectId && series.data && (
          <>
            {totals && (
              <div className="mb-3 flex flex-wrap gap-2 text-xs">
                <KpiPill label="Total" value={totals.total} color="#e6edf3" />
                {(series.data.statuses as StatusKey[]).map((s) => (
                  <KpiPill
                    key={s}
                    label={STATUS_LABELS[s]}
                    value={totals[s] ?? 0}
                    color={STATUS_COLORS[s]}
                  />
                ))}
              </div>
            )}
            <div className="h-80 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={chartData}
                  margin={{ top: 8, right: 8, left: 0, bottom: 8 }}
                >
                  <CartesianGrid stroke="#1f2733" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="_label"
                    tick={{ fill: "#8b949e", fontSize: 11 }}
                    stroke="#1f2733"
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fill: "#8b949e", fontSize: 11 }}
                    stroke="#1f2733"
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#10151c",
                      border: "1px solid #1f2733",
                      borderRadius: 4,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: "#e6edf3" }}
                    itemStyle={{ color: "#e6edf3" }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {(series.data.statuses as StatusKey[]).map((s) => (
                    <Area
                      key={s}
                      type="monotone"
                      dataKey={s}
                      name={STATUS_LABELS[s]}
                      stackId="1"
                      stroke={STATUS_COLORS[s]}
                      fill={STATUS_COLORS[s]}
                      fillOpacity={0.6}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-[11px] text-muted">
              Each point is the count of tasks in that status at end-of-day
              (UTC). History before this feature was enabled is approximated
              from each task's creation and last-update timestamps.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

function KpiPill({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded border border-border bg-panel2 px-2 py-1">
      <span
        aria-hidden="true"
        className="inline-block h-2 w-2 rounded-sm"
        style={{ background: color }}
      />
      <span className="text-muted">{label}</span>
      <span className="font-mono text-text">{value}</span>
    </div>
  );
}
