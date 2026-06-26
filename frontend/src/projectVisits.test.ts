import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  recordVisit,
  getVisitSnapshot,
  computeDelta,
  relativeTime,
} from "./projectVisits";
import type { ProjectStats } from "./api";

function stats(partial: Partial<ProjectStats["by_status"]> & { total?: number }): ProjectStats {
  return {
    by_status: {
      backlog: 0,
      ready: 0,
      in_progress: 0,
      verifying: 0,
      needs_fixes: 0,
      done: 0,
      cancelled: 0,
      ...partial,
    },
    total: partial.total ?? 0,
  } as ProjectStats;
}

describe("projectVisits snapshot roundtrip", () => {
  it("records and reads back a snapshot", () => {
    expect(getVisitSnapshot("p1")).toBeNull();
    recordVisit("p1", stats({ done: 3, total: 10 }));
    const snap = getVisitSnapshot("p1");
    expect(snap?.counts.done).toBe(3);
    expect(snap?.counts.total).toBe(10);
  });

  it("tolerates null stats", () => {
    recordVisit("p2", null);
    expect(getVisitSnapshot("p2")?.counts.total).toBe(0);
  });
});

describe("computeDelta", () => {
  it("returns null without both inputs", () => {
    expect(computeDelta(undefined, null)).toBeNull();
  });

  it("clamps negative deltas to zero", () => {
    recordVisit("p3", stats({ done: 5, needs_fixes: 2, total: 8 }));
    const snap = getVisitSnapshot("p3");
    const delta = computeDelta(stats({ done: 3, needs_fixes: 4, total: 8 }), snap);
    expect(delta).toEqual({ done: 0, needs_fixes: 2, total: 0 });
  });

  it("reports positive progress", () => {
    recordVisit("p4", stats({ done: 1, total: 4 }));
    const snap = getVisitSnapshot("p4");
    const delta = computeDelta(stats({ done: 4, total: 6 }), snap);
    expect(delta).toEqual({ done: 3, needs_fixes: 0, total: 2 });
  });
});

describe("relativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-26T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("handles null / invalid", () => {
    expect(relativeTime(null)).toBeNull();
    expect(relativeTime("not-a-date")).toBeNull();
  });

  it("formats recent and older times", () => {
    expect(relativeTime("2026-06-26T11:59:50Z")).toBe("just now");
    expect(relativeTime("2026-06-26T11:58:00Z")).toBe("2m ago");
    expect(relativeTime("2026-06-26T09:00:00Z")).toBe("3h ago");
    expect(relativeTime("2026-06-24T12:00:00Z")).toBe("2d ago");
  });
});
