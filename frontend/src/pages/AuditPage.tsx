import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AuditEvent, api } from "../api";

type Filter = "all" | "ui" | "auth" | "run";

const FILTERS: { id: Filter; label: string; match: (a: string) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "ui", label: "UI diagnostics", match: (a) => a.startsWith("ui.") },
  { id: "auth", label: "Auth", match: (a) => a.startsWith("auth.") },
  { id: "run", label: "Runs", match: (a) => a.startsWith("run.") },
];

export default function AuditPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const events = useQuery<AuditEvent[]>({
    queryKey: ["audit"],
    queryFn: () => api("/api/v1/audit?limit=500"),
    refetchInterval: 10_000,
  });

  const matcher = FILTERS.find((f) => f.id === filter)?.match ?? (() => true);
  const filtered = useMemo(
    () => (events.data ?? []).filter((e) => matcher(e.action)),
    [events.data, matcher],
  );
  const uiCount = useMemo(
    () => (events.data ?? []).filter((e) => e.action.startsWith("ui.")).length,
    [events.data],
  );

  return (
    <section className="panel">
      <header className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-sm uppercase tracking-wide text-muted">Audit log</h2>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`rounded px-2 py-1 ${
                filter === f.id
                  ? "bg-accent/10 text-accent"
                  : "text-muted hover:text-text"
              }`}
            >
              {f.label}
              {f.id === "ui" && uiCount > 0 && (
                <span className="ml-1 rounded bg-amber-500/20 px-1 text-amber-400">
                  {uiCount}
                </span>
              )}
            </button>
          ))}
        </div>
      </header>
      {events.isError && (
        <p className="text-xs text-danger">{(events.error as Error).message}</p>
      )}
      <div className="-mx-3 overflow-x-auto sm:-mx-4 lg:mx-0">
      <table className="w-full min-w-[720px] text-xs">
        <thead>
          <tr className="text-left uppercase tracking-wide text-muted">
            <th className="px-2 py-1">at</th>
            <th className="px-2 py-1">action</th>
            <th className="px-2 py-1">target</th>
            <th className="px-2 py-1">ip / cert</th>
            <th className="px-2 py-1">payload</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((e) => (
            <tr key={e.id} className="border-t border-border align-top">
              <td className="px-2 py-1 font-mono">{new Date(e.at).toLocaleString()}</td>
              <td
                className={`px-2 py-1 font-mono ${
                  e.action.startsWith("ui.") ? "text-amber-400" : ""
                }`}
              >
                {e.action}
              </td>
              <td className="px-2 py-1 font-mono">
                {e.target_kind ? `${e.target_kind}:${e.target_id?.slice(0, 8)}` : "—"}
              </td>
              <td className="px-2 py-1 text-muted">
                {e.actor_ip || "—"}
                {e.actor_cert_fp && (
                  <div className="text-[11px] sm:text-[10px]">
                    {e.actor_cert_fp.slice(0, 16)}…
                  </div>
                )}
              </td>
              <td className="max-w-[40ch] px-2 py-1 text-muted">
                <PayloadCell payload={e.payload} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  );
}

function PayloadCell({ payload }: { payload: Record<string, any> }) {
  const message = typeof payload?.message === "string" ? payload.message : null;
  const url = typeof payload?.url === "string" ? payload.url : null;
  const runId = typeof payload?.run_id === "string" ? payload.run_id : null;
  const stack = typeof payload?.stack === "string" ? payload.stack : null;
  return (
    <div className="space-y-0.5">
      {message && <div>{message}</div>}
      {runId && <div className="text-[10px] text-muted">run: {runId.slice(0, 8)}</div>}
      {url && <div className="text-[10px] text-muted">url: {url}</div>}
      {stack && (
        <pre className="text-[10px] text-muted whitespace-pre-wrap">
          {stack.split("\n").slice(0, 3).join("\n")}
        </pre>
      )}
      {!message && !runId && !url && !stack && (
        <pre className="text-[10px]">{JSON.stringify(payload)}</pre>
      )}
    </div>
  );
}
