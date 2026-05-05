// Per-project "last seen counts" snapshot. Lives in localStorage so the
// project list can show "+N done since you last opened" without any
// per-user state on the server.
//
// Schema (under one localStorage key):
//   {
//     "<project_id>": { at: "<iso>", counts: {<status>: number, total: number} }
//   }
//
// Snapshot is updated on ProjectPage mount.

import type { ProjectStats } from "./api";

const STORAGE_KEY = "daedalus.project_visits.v1";

export interface VisitSnapshot {
  at: string; // ISO timestamp of the last visit
  counts: ProjectStats["by_status"] & { total: number };
}

type AllVisits = Record<string, VisitSnapshot>;

function readAll(): AllVisits {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as AllVisits;
  } catch {
    return {};
  }
}

function writeAll(all: AllVisits): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* quota exceeded — drop oldest keys */
    const trimmed = Object.entries(all)
      .sort((a, b) => (b[1].at < a[1].at ? -1 : 1))
      .slice(0, 50);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(trimmed)));
    } catch {
      /* give up */
    }
  }
}

export function getVisitSnapshot(projectId: string): VisitSnapshot | null {
  return readAll()[projectId] ?? null;
}

export function recordVisit(projectId: string, stats: ProjectStats | null | undefined): void {
  const all = readAll();
  all[projectId] = {
    at: new Date().toISOString(),
    counts: {
      backlog: stats?.by_status.backlog ?? 0,
      ready: stats?.by_status.ready ?? 0,
      in_progress: stats?.by_status.in_progress ?? 0,
      verifying: stats?.by_status.verifying ?? 0,
      needs_fixes: stats?.by_status.needs_fixes ?? 0,
      done: stats?.by_status.done ?? 0,
      cancelled: stats?.by_status.cancelled ?? 0,
      total: stats?.total ?? 0,
    },
  };
  writeAll(all);
}

export interface VisitDelta {
  done: number;
  needs_fixes: number;
  total: number;
}

/** Compute the delta between current stats and the last snapshot. Negative
 * deltas (tasks deleted) are clamped to 0 so the UI doesn't get confusing. */
export function computeDelta(
  current: ProjectStats | undefined,
  snapshot: VisitSnapshot | null,
): VisitDelta | null {
  if (!current || !snapshot) return null;
  return {
    done: Math.max(0, current.by_status.done - snapshot.counts.done),
    needs_fixes: Math.max(
      0,
      current.by_status.needs_fixes - snapshot.counts.needs_fixes,
    ),
    total: Math.max(0, current.total - snapshot.counts.total),
  };
}

/** Best-effort relative time string ("2h ago"). */
export function relativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const diffSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diffSec < 30) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mon = Math.floor(day / 30);
  if (mon < 12) return `${mon}mo ago`;
  return `${Math.floor(mon / 12)}y ago`;
}
