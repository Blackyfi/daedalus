import { Link } from "react-router-dom";
import type {
  ActiveRunner,
  GitStatusInfo,
  Project,
  ProjectStats,
} from "../api";
import {
  computeDelta,
  getVisitSnapshot,
  relativeTime,
  type VisitSnapshot,
} from "../projectVisits";

interface Props {
  project: Project;
  stats: ProjectStats | undefined;
  activeRun: ActiveRunner | undefined;
  gitStatus: GitStatusInfo | undefined;
}

type Tone =
  | "running"
  | "needs_attention"
  | "queued"
  | "idle_done"
  | "idle_empty"
  | "archived";

interface ToneInfo {
  label: string;
  pillClass: string;
  cardBorderClass: string;
  dotClass: string;
}

const TONE: Record<Tone, ToneInfo> = {
  running: {
    label: "running",
    pillClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    cardBorderClass: "border-emerald-500/40",
    dotClass: "bg-emerald-400 animate-pulse",
  },
  needs_attention: {
    label: "needs fix",
    pillClass: "bg-rose-500/15 text-rose-300 border-rose-500/40",
    cardBorderClass: "border-rose-500/30",
    dotClass: "bg-rose-400",
  },
  queued: {
    label: "queued",
    pillClass: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    cardBorderClass: "border-amber-500/30",
    dotClass: "bg-amber-400",
  },
  idle_done: {
    label: "all done",
    pillClass: "bg-slate-500/15 text-slate-300 border-slate-500/30",
    cardBorderClass: "border-border",
    dotClass: "bg-slate-400",
  },
  idle_empty: {
    label: "empty",
    pillClass: "bg-slate-700/30 text-slate-400 border-slate-700/40",
    cardBorderClass: "border-border",
    dotClass: "bg-slate-600",
  },
  archived: {
    label: "archived",
    pillClass:
      "bg-slate-800/40 text-slate-500 border-slate-700/40 line-through",
    cardBorderClass: "border-border opacity-60",
    dotClass: "bg-slate-600",
  },
};

function toneFor(
  project: Project,
  stats: ProjectStats | undefined,
  activeRun: ActiveRunner | undefined,
): Tone {
  if (project.archived) return "archived";
  if (activeRun) return "running";
  const by = stats?.by_status;
  if (!stats || stats.total === 0) return "idle_empty";
  if ((by?.needs_fixes ?? 0) > 0) return "needs_attention";
  if (
    (by?.in_progress ?? 0) +
      (by?.verifying ?? 0) +
      (by?.ready ?? 0) +
      (by?.backlog ?? 0) >
    0
  )
    return "queued";
  return "idle_done";
}

interface KpiBadgeProps {
  label: string;
  value: number;
  tone: "muted" | "amber" | "rose" | "emerald" | "accent";
}

function KpiBadge({ label, value, tone }: KpiBadgeProps) {
  if (value <= 0) return null;
  const palette: Record<KpiBadgeProps["tone"], string> = {
    muted: "bg-slate-800/40 text-slate-300 border-slate-700/40",
    amber: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    rose: "bg-rose-500/10 text-rose-300 border-rose-500/30",
    emerald: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
    accent: "bg-accent/10 text-accent border-accent/30",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${palette[tone]}`}
    >
      <span className="font-semibold">{value}</span>
      <span>{label}</span>
    </span>
  );
}

export default function ProjectCard({
  project,
  stats,
  activeRun,
  gitStatus,
}: Props) {
  const tone = toneFor(project, stats, activeRun);
  const toneInfo = TONE[tone];
  const pullRequired = !!gitStatus?.needs_pull;
  const snapshot: VisitSnapshot | null = getVisitSnapshot(project.id);
  const delta = computeDelta(stats, snapshot);
  const lastVisitAgo = relativeTime(snapshot?.at);
  const lastActivityAgo = relativeTime(stats?.last_activity_at);

  return (
    <Link
      to={`/projects/${project.id}`}
      className={`block rounded border bg-panel p-3 transition-colors hover:border-accent ${toneInfo.cardBorderClass}`}
    >
      {/* Header row: name + status pill */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${toneInfo.dotClass}`}
            />
            <span className="truncate text-sm font-semibold">{project.name}</span>
          </div>
          <div className="truncate text-[11px] text-muted">
            {project.workspace_path}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {pullRequired && (
            <span
              className="rounded border border-rose-500/60 bg-rose-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-rose-300"
              title={`${gitStatus?.behind_count} commit(s) behind ${gitStatus?.upstream ?? "upstream"} — git pull required before launching agents`}
            >
              ⚠ pull
            </span>
          )}
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${toneInfo.pillClass}`}
          >
            {toneInfo.label}
          </span>
        </div>
      </div>

      {/* Active-run line */}
      {activeRun && (
        <div className="mt-1.5 truncate text-xs text-emerald-300">
          ▶ {activeRun.run_kind}: {activeRun.task_title ?? activeRun.run_id.slice(0, 8)}
        </div>
      )}

      {/* KPI badge row */}
      {stats && stats.total > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          <KpiBadge label="Backlog" value={stats.by_status.backlog} tone="muted" />
          <KpiBadge label="Ready" value={stats.by_status.ready} tone="amber" />
          <KpiBadge
            label="Active"
            value={stats.by_status.in_progress + stats.by_status.verifying}
            tone="emerald"
          />
          <KpiBadge label="Needs fix" value={stats.by_status.needs_fixes} tone="rose" />
          <KpiBadge label="Done" value={stats.by_status.done} tone="accent" />
          <span className="ml-auto text-[10px] text-muted">{stats.total} tasks</span>
        </div>
      )}

      {/* Footer: deltas + activity */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted">
        {delta && delta.done > 0 && (
          <span className="text-emerald-400">
            +{delta.done} done since you last opened
            {lastVisitAgo ? ` (${lastVisitAgo})` : ""}
          </span>
        )}
        {delta && delta.needs_fixes > 0 && (
          <span className="text-rose-400">+{delta.needs_fixes} new fix</span>
        )}
        {delta && delta.total > 0 && delta.done === 0 && (
          <span>+{delta.total} new tasks</span>
        )}
        {lastActivityAgo && <span>last activity {lastActivityAgo}</span>}
        {!snapshot && stats && stats.total > 0 && (
          <span className="italic">first visit</span>
        )}
      </div>

      {/* Description */}
      {project.description && (
        <p className="mt-2 line-clamp-2 text-xs text-muted">{project.description}</p>
      )}
    </Link>
  );
}
