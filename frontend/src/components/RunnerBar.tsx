import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, RunnerSnapshot } from "../api";

function elapsed(startedAt: string | null): string {
  if (!startedAt) return "—";
  const ms = Date.now() - new Date(startedAt).getTime();
  if (ms < 0) return "now";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

export default function RunnerBar() {
  const snap = useQuery<RunnerSnapshot>({
    queryKey: ["runner-snapshot"],
    queryFn: () => api("/api/v1/system/runners"),
    refetchInterval: 5_000,
  });

  const data = snap.data;
  if (!data) return null;

  const cap = data.max_concurrent_projects;
  const used = data.active_count;
  const tone =
    used === 0
      ? "text-muted"
      : used >= cap
        ? "text-amber-400"
        : "text-accent";

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={tone}>
        {used}/{cap} project{cap === 1 ? "" : "s"} running
      </span>
      <div className="flex flex-wrap gap-1">
        {data.active.map((a) => (
          <Link
            key={a.run_id}
            to={`/projects/${a.project_id}/runs/${a.run_id}`}
            className="flex items-center gap-1 rounded border border-border bg-panel2 px-2 py-0.5 hover:border-accent"
            title={a.task_title ?? a.run_kind}
          >
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            <span className="font-semibold">{a.project_name}</span>
            <span className="text-muted">{elapsed(a.started_at)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
