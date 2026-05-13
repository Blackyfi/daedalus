/**
 * ProjectActionBar — a compact inbox that surfaces every actionable thing on
 * a project: an active run, plan proposals waiting for review, tasks that
 * need fixes, merge batches awaiting review or resolving conflicts.
 *
 * Renders nothing when there are no actionable items, so it adds zero chrome
 * on first-time / quiet projects. Each tile has a single click target with
 * a clear destination.
 */
import type { ReactNode } from "react";

export interface ActionItem {
  key: string;
  icon: ReactNode;
  label: ReactNode;
  count?: number;
  tone: "info" | "warn" | "danger" | "success";
  onClick: () => void;
  title?: string;
}

const TONE_CLASSES: Record<ActionItem["tone"], string> = {
  info: "bg-info/10 text-info hover:bg-info/20 border-info/30",
  warn: "bg-warning/10 text-warning hover:bg-warning/20 border-warning/30",
  danger: "bg-danger/10 text-danger hover:bg-danger/20 border-danger/30",
  success: "bg-success/10 text-success hover:bg-success/20 border-success/30",
};

export default function ProjectActionBar({ items }: { items: ActionItem[] }) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label="Project actions"
      className="flex flex-wrap items-center gap-2"
    >
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          onClick={it.onClick}
          title={it.title}
          className={
            "inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
            TONE_CLASSES[it.tone]
          }
        >
          <span className="text-base leading-none" aria-hidden="true">
            {it.icon}
          </span>
          {it.count !== undefined && (
            <span className="text-sm font-semibold tabular-nums">{it.count}</span>
          )}
          <span className="font-medium">{it.label}</span>
        </button>
      ))}
    </nav>
  );
}
