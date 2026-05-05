import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, SubscriptionInfo } from "../api";

const PLAN_LABEL: Record<string, string> = {
  pro: "Pro",
  pro_max: "Pro Max",
  max_5x: "Max 5x",
  max_20x: "Max 20x",
  team: "Team",
  enterprise: "Enterprise",
  unknown: "Unknown",
};

function colorFor(pct: number | null): string {
  if (pct == null) return "text-muted";
  if (pct < 60) return "text-accent";
  if (pct < 85) return "text-amber-400";
  return "text-danger";
}

function dotFor(info: SubscriptionInfo): string {
  if (info.kind === "ok") {
    const pct = Math.max(info.weekly_used_pct ?? 0, info.five_hour_used_pct ?? 0);
    if (pct >= 95) return "bg-danger";
    if (pct >= 85) return "bg-amber-500";
    if (pct >= 60) return "bg-amber-400";
    return "bg-accent";
  }
  if (info.kind === "stale_or_missing") return "bg-muted";
  if (info.kind === "auth_required" || info.kind === "cli_missing") return "bg-amber-500";
  return "bg-danger";
}

function shortLabel(info: SubscriptionInfo): string {
  if (info.kind === "ok") {
    const tier = info.plan_tier ? PLAN_LABEL[info.plan_tier] ?? info.plan : info.plan;
    return tier || "Subscription";
  }
  if (info.kind === "stale_or_missing") return "Subscription: probing…";
  if (info.kind === "cli_missing") return "claude CLI missing";
  if (info.kind === "auth_required") return "Login required";
  if (info.kind === "timeout") return "Probe timed out";
  return "Subscription: error";
}

export default function SubscriptionChip() {
  const [open, setOpen] = useState(false);
  const sub = useQuery<SubscriptionInfo>({
    queryKey: ["subscription"],
    queryFn: () => api("/api/v1/system/subscription"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  if (!sub.data) {
    return (
      <span className="rounded border border-border bg-panel2 px-2 py-1 text-xs text-muted">
        Subscription: loading…
      </span>
    );
  }
  const info = sub.data;
  const weekly = info.weekly_used_pct;
  const fiveH = info.five_hour_used_pct;

  return (
    <div className="relative">
      <button
        className="flex items-center gap-2 rounded border border-border bg-panel2 px-2 py-1 text-xs hover:border-accent"
        onClick={() => setOpen((s) => !s)}
        title={info.email ?? undefined}
      >
        <span className={`inline-block h-2 w-2 rounded-full ${dotFor(info)}`} />
        <span className="font-semibold">{shortLabel(info)}</span>
        {weekly != null && (
          <span className={colorFor(weekly)}>
            wk {Math.round(weekly)}%
          </span>
        )}
        {fiveH != null && (
          <span className={colorFor(fiveH)}>
            5h {Math.round(fiveH)}%
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-[340px] rounded border border-border bg-panel p-3 text-xs shadow-lg">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-xs uppercase tracking-wide text-muted">Subscription</h3>
            <button className="btn" onClick={() => setOpen(false)}>
              close
            </button>
          </header>
          <dl className="space-y-1">
            <Row label="Plan" value={info.plan ?? "unknown"} />
            <Row label="Account" value={info.email ?? "—"} />
            <Row label="Status" value={info.kind} />
            {weekly != null && (
              <Row
                label="Weekly used"
                value={
                  <span className={colorFor(weekly)}>
                    {weekly.toFixed(1)}%
                    {info.weekly_resets_in ? ` · resets in ${info.weekly_resets_in}` : ""}
                  </span>
                }
              />
            )}
            {fiveH != null && (
              <Row
                label="5-hour used"
                value={
                  <span className={colorFor(fiveH)}>
                    {fiveH.toFixed(1)}%
                    {info.five_hour_resets_in ? ` · resets in ${info.five_hour_resets_in}` : ""}
                  </span>
                }
              />
            )}
            <Row
              label="Refreshed"
              value={info.fetched_at ? new Date(info.fetched_at).toLocaleString() : "—"}
            />
            {info.error && (
              <Row label="Error" value={<span className="text-danger">{info.error}</span>} />
            )}
          </dl>
          {info.kind !== "ok" && info.raw_text && (
            <pre className="mt-2 max-h-[160px] overflow-auto rounded border border-border bg-bg p-2 text-[10px] text-muted whitespace-pre-wrap">
              {info.raw_text.slice(0, 2000)}
            </pre>
          )}
          <p className="mt-2 text-[10px] text-muted">
            Pythia probes <code>claude /status</code> from inside the Talos container every
            ~10 minutes; this snapshot is cached in Redis.
          </p>
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}
