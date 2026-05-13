import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiJson } from "../api";
import { useApp } from "../store";

interface Props {
  open: boolean;
  onClose: () => void;
  projectId: string;
  initialBatchId?: string | null;
}

type Category =
  | "clean"
  | "conflict"
  | "empty"
  | "already_merged"
  | "missing_branch"
  | "missing_run";

type ItemState =
  | "pending"
  | "merged"
  | "skipped_empty"
  | "skipped_already_merged"
  | "skipped_missing"
  | "skipped_conflict"
  | "resolution_queued"
  | "resolution_running"
  | "resolved"
  | "resolution_failed";

type BatchState =
  | "pending"
  | "merging_clean"
  | "awaiting_review"
  | "resolving"
  | "shipping"
  | "shipped"
  | "failed"
  | "aborted";

interface PreviewPlan {
  task_id: string;
  task_title: string;
  run_id: string | null;
  branch: string;
  argus_verdict: string | null;
  category: string;
  conflicting_files: string[];
  commits_ahead: number;
  files_changed: number;
}

interface MergePreview {
  project_id: string;
  project_name: string;
  workspace_path: string;
  default_branch: string;
  proposed_integration_branch: string;
  plans: PreviewPlan[];
}

interface BatchItem {
  id: string;
  task_id: string | null;
  branch: string;
  category: Category;
  state: ItemState;
  conflicting_files: string[];
  commits_ahead: number;
  files_changed: number;
  error: string | null;
  resolution_task_id: string | null;
  resolution_run_id: string | null;
}

interface MergeBatch {
  id: string;
  project_id: string;
  integration_branch: string;
  integration_worktree: string;
  state: BatchState;
  verify_exit_code: number | null;
  verify_output: string | null;
  error: string | null;
  require_argus_pass: boolean;
  created_at: string;
  updated_at: string;
  shipped_at: string | null;
  items: BatchItem[];
  counts: Record<string, number>;
}

interface ResolutionStep {
  item_id: string;
  branch: string;
  state: string;
  task_id: string | null;
  run_id: string | null;
  error: string | null;
}

interface ShipResult {
  state: string;
  integration_branch: string;
  default_branch: string;
  pruned_branches: string[];
  removed_worktree: boolean;
  error: string | null;
}

const CATEGORY_LABEL: Record<string, string> = {
  clean: "clean",
  conflict: "conflict",
  empty: "empty",
  already_merged: "already merged",
  "already-merged": "already merged",
  missing_branch: "missing branch",
  "missing-branch": "missing branch",
  missing_run: "no run",
  "missing-run": "no run",
};

const CATEGORY_BADGE: Record<string, string> = {
  clean: "bg-success/15 text-success",
  conflict: "bg-danger/15 text-danger",
  empty: "bg-muted/15 text-muted",
  already_merged: "bg-muted/15 text-muted",
  "already-merged": "bg-muted/15 text-muted",
  missing_branch: "bg-warning/15 text-warning",
  "missing-branch": "bg-warning/15 text-warning",
  missing_run: "bg-warning/15 text-warning",
  "missing-run": "bg-warning/15 text-warning",
};

const ITEM_STATE_BADGE: Record<ItemState, string> = {
  pending: "bg-muted/15 text-muted",
  merged: "bg-success/15 text-success",
  skipped_empty: "bg-muted/15 text-muted",
  skipped_already_merged: "bg-muted/15 text-muted",
  skipped_missing: "bg-warning/15 text-warning",
  skipped_conflict: "bg-danger/15 text-danger",
  resolution_queued: "bg-warning/15 text-warning",
  resolution_running: "bg-info/15 text-info",
  resolved: "bg-success/15 text-success",
  resolution_failed: "bg-danger/15 text-danger",
};


export default function MergeBatchModal({ open, onClose, projectId, initialBatchId }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [requireArgusPass, setRequireArgusPass] = useState(true);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);

  const preview = useQuery<MergePreview>({
    queryKey: ["merge-preview", projectId, requireArgusPass],
    queryFn: () =>
      apiJson<MergePreview>(
        `/api/v1/projects/${projectId}/merge-batch/preview`,
        { require_argus_pass: requireArgusPass },
      ),
    enabled: open && !activeBatchId,
    refetchOnWindowFocus: false,
  });

  const batch = useQuery<MergeBatch>({
    queryKey: ["merge-batch", activeBatchId],
    queryFn: () =>
      api<MergeBatch>(
        `/api/v1/projects/${projectId}/merge-batches/${activeBatchId}`,
      ),
    enabled: open && !!activeBatchId,
    refetchInterval: (q) => {
      const data = q.state.data as MergeBatch | undefined;
      if (!data) return false;
      // Poll while there's work in flight on resolution runs.
      const stateInProgress =
        data.state === "merging_clean" ||
        data.state === "resolving" ||
        data.state === "shipping";
      const itemsInProgress = data.items.some(
        (i) =>
          i.state === "resolution_queued" || i.state === "resolution_running",
      );
      return stateInProgress || itemsInProgress ? 3000 : false;
    },
    refetchOnWindowFocus: false,
  });

  const create = useMutation<MergeBatch, Error, void>({
    mutationFn: () =>
      apiJson<MergeBatch>(`/api/v1/projects/${projectId}/merge-batch`, {
        require_argus_pass: requireArgusPass,
      }),
    onSuccess: (b) => {
      setActiveBatchId(b.id);
      qc.invalidateQueries({ queryKey: ["git-status", projectId] });
      qc.setQueryData(["merge-batch", b.id], b);
      flash(`Batch ${b.integration_branch.slice(-8)} created`, "success");
    },
    onError: (err) => flash(err.message || "Merge failed", "error"),
  });

  const resolveNext = useMutation<ResolutionStep | null, Error, void>({
    mutationFn: () =>
      apiJson<ResolutionStep | null>(
        `/api/v1/projects/${projectId}/merge-batches/${activeBatchId}/resolve`,
        {},
      ),
    onSuccess: (step) => {
      qc.invalidateQueries({ queryKey: ["merge-batch", activeBatchId] });
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      qc.invalidateQueries({ queryKey: ["runs", projectId] });
      if (!step) {
        flash("No conflicts left to resolve", "info");
        return;
      }
      if (step.state === "queued") {
        flash(`Queued resolver agent for ${step.branch}`, "success");
      } else if (step.state === "auto-merged") {
        flash(
          `Conflict resolved itself after earlier merges (${step.branch})`,
          "success",
        );
      } else if (step.state === "failed") {
        flash(step.error || "Resolution failed", "error");
      } else {
        flash(`Resolution: ${step.state}`, "info");
      }
    },
    onError: (err) => flash(err.message || "Resolve failed", "error"),
  });

  const ship = useMutation<ShipResult, Error, void>({
    mutationFn: () =>
      apiJson<ShipResult>(
        `/api/v1/projects/${projectId}/merge-batches/${activeBatchId}/ship`,
        { delete_source_branches: true, remove_worktree: true },
      ),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["merge-batch", activeBatchId] });
      qc.invalidateQueries({ queryKey: ["git-status", projectId] });
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      if (result.state === "shipped") {
        flash(
          `Shipped to ${result.default_branch} · pruned ${result.pruned_branches.length} branch(es)`,
          "success",
        );
      } else {
        flash(result.error || "Ship failed", "error");
      }
    },
    onError: (err) => flash(err.message || "Ship failed", "error"),
  });

  // Reset which batch is active only when the modal opens or the caller
  // hands us a new initial batch. Keeping `onClose` out of the deps matters:
  // parents re-render frequently (polling queries), and a fresh arrow each
  // render would otherwise stomp on activeBatchId mid-flow and snap the user
  // back to the preview view right after a batch is created.
  useEffect(() => {
    if (!open) return;
    setActiveBatchId(initialBatchId ?? null);
  }, [open, initialBatchId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Auto-resolve loop: once a batch is in `resolving` and the previous
  // resolver run has finished, kick off the next one without a manual click.
  // The user opted into this when they hit "Start merge", so we don't pester
  // them again per conflict. Stops as soon as nothing is pending.
  const batchData = batch.data;
  useEffect(() => {
    if (!batchData) return;
    if (batchData.state !== "resolving") return;
    if (resolveNext.isPending) return;
    const inFlight = batchData.items.some(
      (i) =>
        i.state === "resolution_queued" || i.state === "resolution_running",
    );
    if (inFlight) return;
    const pending = batchData.items.some((i) => i.state === "skipped_conflict");
    if (!pending) return;
    resolveNext.mutate();
  }, [batchData, resolveNext]);

  if (!open) return null;

  const cleanCount =
    preview.data?.plans.filter((p) => p.category === "clean").length ?? 0;
  const conflictCount =
    preview.data?.plans.filter((p) => p.category === "conflict").length ?? 0;
  const skippedCount =
    (preview.data?.plans.length ?? 0) - cleanCount - conflictCount;
  const mergeableCount = cleanCount + conflictCount;

  // Linear step derived purely from batch state. Keeps the UI honest:
  // whatever the backend says trumps any local intent.
  type Step = "preview" | "resolving" | "ship" | "shipping" | "done" | "failed";
  const step: Step = !batchData
    ? "preview"
    : batchData.state === "shipped"
      ? "done"
      : batchData.state === "shipping"
        ? "shipping"
        : batchData.state === "failed" || batchData.state === "aborted"
          ? "failed"
          : batchData.state === "awaiting_review"
            ? "ship"
            : "resolving";

  const defaultBranch =
    preview.data?.default_branch ?? batchData?.integration_branch.split("-")[0] ?? "main";

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="merge-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="panel flex w-full max-h-[90vh] max-w-4xl flex-col sm:max-h-[85vh]">
        <header className="mb-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2
              id="merge-title"
              className="text-sm uppercase tracking-wide text-muted"
            >
              Ship done tasks to <code>{defaultBranch}</code>
            </h2>
            <p className="mt-0.5 hidden text-xs text-muted sm:block">
              Merges your done branches into an integration branch first, runs
              verify, resolves any conflicts with an agent, then fast-forwards{" "}
              <code>{defaultBranch}</code>. Nothing touches{" "}
              <code>{defaultBranch}</code> until you click Ship.
            </p>
          </div>
          <button className="btn shrink-0" onClick={onClose} aria-label="Close">
            close
          </button>
        </header>

        <Stepper step={step} />

        {step === "preview" && (
          <>
            <label className="mb-2 flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={requireArgusPass}
                onChange={(e) => setRequireArgusPass(e.target.checked)}
              />
              Only include tasks whose latest Argus verdict is{" "}
              <code className="rounded bg-success/15 px-1 text-success">
                pass
              </code>{" "}
              (or no verdict — Argus disabled).
            </label>

            {preview.isLoading ? (
              <p className="text-sm text-muted">Pre-flighting branches…</p>
            ) : preview.error ? (
              <p className="text-sm text-danger">
                Preview failed: {(preview.error as Error).message}
              </p>
            ) : preview.data ? (
              <PreviewBody preview={preview.data} />
            ) : null}
          </>
        )}

        {batchData && step !== "preview" && (
          <BatchBody batch={batchData} step={step} />
        )}

        <footer className="mt-3 flex flex-wrap items-center justify-end gap-2 border-t border-muted/20 pt-3">
          {step === "preview" && preview.data && (
            <p className="mr-auto text-xs text-muted">
              <span className="font-medium text-fg">
                {mergeableCount} mergeable
              </span>
              {skippedCount > 0 && ` · ${skippedCount} skipped`}
            </p>
          )}

          {step === "preview" && (
            <>
              <button className="btn" onClick={onClose}>
                cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={create.isPending || mergeableCount === 0}
                title={
                  mergeableCount === 0
                    ? "Nothing to merge — relax the Argus filter"
                    : `Merge ${mergeableCount} branch${mergeableCount === 1 ? "" : "es"} into an integration branch, resolve any conflicts, then ship to ${defaultBranch}`
                }
                onClick={() => create.mutate()}
              >
                {create.isPending
                  ? "Starting…"
                  : `Start merge (${mergeableCount})`}
              </button>
            </>
          )}

          {batchData && step === "resolving" && (
            <ResolvingFooter
              batch={batchData}
              resolving={resolveNext.isPending}
              onClose={onClose}
            />
          )}

          {batchData && step === "ship" && (
            <ShipFooter
              batch={batchData}
              shipping={ship.isPending}
              defaultBranch={defaultBranch}
              onShip={() => ship.mutate()}
              onClose={onClose}
            />
          )}

          {batchData && step === "shipping" && (
            <p className="mr-auto text-xs text-muted">
              Fast-forwarding <code>{defaultBranch}</code>…
            </p>
          )}

          {batchData && step === "done" && (
            <DoneFooter
              batch={batchData}
              defaultBranch={defaultBranch}
              onClose={onClose}
            />
          )}

          {batchData && step === "failed" && (
            <FailedFooter batch={batchData} onClose={onClose} />
          )}
        </footer>
      </div>
    </div>
  );
}

function Stepper({
  step,
}: {
  step: "preview" | "resolving" | "ship" | "shipping" | "done" | "failed";
}) {
  // Map every state onto a 0/1/2 index so progress is monotonic visually,
  // even for terminal states.
  const idx =
    step === "preview"
      ? 0
      : step === "resolving"
        ? 1
        : step === "ship" || step === "shipping"
          ? 2
          : 2;
  const labels = [
    { key: "preview", label: "Preview" },
    { key: "resolving", label: "Merge & resolve" },
    { key: "ship", label: "Ship" },
  ];
  return (
    <ol className="mb-3 flex items-center gap-1 text-[11px]">
      {labels.map((l, i) => {
        const active = i === idx && step !== "done" && step !== "failed";
        const complete =
          i < idx ||
          step === "done" ||
          (step === "ship" && i <= 1) ||
          (step === "shipping" && i <= 1);
        const failed = step === "failed" && i === idx;
        const tone = failed
          ? "bg-danger/20 text-danger"
          : step === "done" && i === 2
            ? "bg-success/20 text-success"
            : complete
              ? "bg-success/15 text-success"
              : active
                ? "bg-info/20 text-info"
                : "bg-muted/15 text-muted";
        return (
          <li key={l.key} className="flex items-center gap-1">
            <span
              className={`rounded px-2 py-0.5 ${tone} ${active ? "font-semibold" : ""}`}
            >
              {i + 1}. {l.label}
            </span>
            {i < labels.length - 1 && (
              <span className="text-muted">›</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function PreviewBody({ preview }: { preview: MergePreview }) {
  const [showSkipped, setShowSkipped] = useState(false);
  if (preview.plans.length === 0) {
    return (
      <p className="text-sm text-muted">
        No <code>done</code> tasks match the filter for this project.
      </p>
    );
  }
  const actionable = preview.plans.filter(
    (p) => p.category === "clean" || p.category === "conflict",
  );
  const skipped = preview.plans.filter(
    (p) => p.category !== "clean" && p.category !== "conflict",
  );
  return (
    <div className="flex-1 overflow-auto">
      {actionable.length === 0 ? (
        <p className="text-sm text-muted">
          No mergeable branches — every <code>done</code> task is either empty,
          already in <code>{preview.default_branch}</code>, or missing a branch.
        </p>
      ) : (
        <PreviewTable plans={actionable} />
      )}
      {skipped.length > 0 && (
        <div className="mt-3 border-t border-muted/10 pt-2">
          <button
            type="button"
            className="text-[11px] text-muted underline-offset-2 hover:underline"
            onClick={() => setShowSkipped((v) => !v)}
          >
            {showSkipped ? "Hide" : "Show"} {skipped.length} skipped (already in{" "}
            <code>{preview.default_branch}</code>, empty, or missing branch)
          </button>
          {showSkipped && <PreviewTable plans={skipped} compact />}
        </div>
      )}
    </div>
  );
}

function PreviewTable({
  plans,
  compact = false,
}: {
  plans: PreviewPlan[];
  compact?: boolean;
}) {
  return (
    <table className={`w-full text-xs ${compact ? "opacity-70" : ""}`}>
      <thead className="sticky top-0 bg-bg">
        <tr className="text-left text-muted">
          <th className="pb-1 pr-2">Task</th>
          <th className="pb-1 pr-2">Argus</th>
          <th className="pb-1 pr-2">Status</th>
          <th className="pb-1 pr-2">Commits</th>
          <th className="pb-1 pr-2">Files</th>
        </tr>
      </thead>
      <tbody>
        {plans.map((p) => (
          <tr key={p.task_id} className="border-t border-muted/10 align-top">
            <td className="py-1 pr-2">
              <div className="font-medium">{p.task_title}</div>
              <code className="text-[10px] text-muted">{p.branch}</code>
            </td>
            <td className="py-1 pr-2">
              <span className="text-[11px] text-muted">
                {p.argus_verdict ?? "—"}
              </span>
            </td>
            <td className="py-1 pr-2">
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] ${CATEGORY_BADGE[p.category] ?? "bg-muted/15 text-muted"}`}
              >
                {CATEGORY_LABEL[p.category] ?? p.category}
              </span>
              {p.category === "conflict" &&
                p.conflicting_files.length > 0 && (
                  <ul className="mt-1 ml-4 list-disc text-[10px] text-danger">
                    {p.conflicting_files.slice(0, 5).map((f) => (
                      <li key={f}>
                        <code>{f}</code>
                      </li>
                    ))}
                    {p.conflicting_files.length > 5 && (
                      <li>+{p.conflicting_files.length - 5} more</li>
                    )}
                  </ul>
                )}
            </td>
            <td className="py-1 pr-2 tabular-nums">{p.commits_ahead}</td>
            <td className="py-1 pr-2 tabular-nums">{p.files_changed}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BatchBody({
  batch,
  step,
}: {
  batch: MergeBatch;
  step: "resolving" | "ship" | "shipping" | "done" | "failed";
}) {
  const merged = batch.items.filter((i) => i.state === "merged");
  const resolved = batch.items.filter((i) => i.state === "resolved");
  const inFlight = batch.items.filter(
    (i) =>
      i.state === "resolution_queued" || i.state === "resolution_running",
  );
  const pending = batch.items.filter((i) => i.state === "skipped_conflict");
  const failed = batch.items.filter((i) => i.state === "resolution_failed");
  const skipped = batch.items.filter(
    (i) =>
      i.state === "skipped_empty" ||
      i.state === "skipped_already_merged" ||
      i.state === "skipped_missing",
  );

  // Items the user actually needs to think about right now.
  const focus = [...inFlight, ...pending, ...failed];

  return (
    <div className="flex-1 overflow-auto">
      {batch.error && (
        <p className="mb-2 rounded bg-danger/10 p-2 text-sm text-danger">
          {batch.error}
        </p>
      )}

      <StatusBanner
        step={step}
        merged={merged.length}
        resolved={resolved.length}
        inFlight={inFlight.length}
        pending={pending.length}
        failed={failed.length}
      />

      {focus.length > 0 && (
        <BatchItemTable items={focus} projectId={batch.project_id} />
      )}

      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted">
          Details · {merged.length} merged
          {resolved.length > 0 && ` · ${resolved.length} agent-resolved`}
          {skipped.length > 0 && ` · ${skipped.length} skipped`}
        </summary>
        <div className="mt-1">
          <BatchItemTable
            items={[...merged, ...resolved, ...skipped]}
            projectId={batch.project_id}
            compact
          />
        </div>
      </details>

      <p className="mt-3 text-[10px] text-muted">
        Integration branch{" "}
        <code className="rounded bg-success/15 px-1 text-success">
          {batch.integration_branch}
        </code>
      </p>

      {batch.verify_output && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">
            verify_commands{" "}
            {batch.verify_exit_code === 0
              ? "✓ exit 0"
              : `✗ exit ${batch.verify_exit_code}`}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-bg-2 p-2 text-[10px]">
            {batch.verify_output}
          </pre>
        </details>
      )}
    </div>
  );
}

function StatusBanner({
  step,
  merged,
  resolved,
  inFlight,
  pending,
  failed,
}: {
  step: "resolving" | "ship" | "shipping" | "done" | "failed";
  merged: number;
  resolved: number;
  inFlight: number;
  pending: number;
  failed: number;
}) {
  if (step === "done") {
    return (
      <div className="rounded bg-success/10 p-3 text-sm">
        <span className="font-semibold text-success">✓ Shipped.</span>{" "}
        {merged + resolved} branch{merged + resolved === 1 ? "" : "es"} landed on
        main.
      </div>
    );
  }
  if (step === "failed") {
    return (
      <div className="rounded bg-danger/10 p-3 text-sm text-danger">
        Batch failed.
        {failed > 0 && ` ${failed} resolution${failed === 1 ? "" : "s"} failed.`}
      </div>
    );
  }
  if (step === "ship") {
    return (
      <div className="rounded bg-success/10 p-3 text-sm">
        <span className="font-semibold text-success">
          Ready to ship.
        </span>{" "}
        {merged + resolved} branch{merged + resolved === 1 ? "" : "es"}{" "}
        integrated.{" "}
        {failed > 0 && (
          <span className="text-warning">
            ({failed} resolution{failed === 1 ? "" : "s"} failed — review below)
          </span>
        )}
      </div>
    );
  }
  if (step === "shipping") {
    return (
      <div className="rounded bg-info/10 p-3 text-sm text-info">
        Fast-forwarding main and pruning merged branches…
      </div>
    );
  }
  // resolving
  const parts: string[] = [];
  if (merged > 0) parts.push(`${merged} merged`);
  if (inFlight > 0)
    parts.push(`${inFlight} resolver run${inFlight === 1 ? "" : "s"} in flight`);
  if (pending > 0)
    parts.push(`${pending} queued for resolver`);
  if (resolved > 0) parts.push(`${resolved} resolved`);
  return (
    <div className="rounded bg-info/10 p-3 text-sm text-info">
      <span className="font-semibold">Resolving conflicts.</span>{" "}
      {parts.join(" · ") || "Working…"}
      {(inFlight > 0 || pending > 0) && (
        <span className="ml-1 text-xs opacity-70">
          (auto — agent resolves each conflict sequentially)
        </span>
      )}
    </div>
  );
}

function BatchItemTable({
  items,
  projectId,
  compact = false,
}: {
  items: BatchItem[];
  projectId: string;
  compact?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <table className={`mt-2 w-full text-xs ${compact ? "opacity-80" : ""}`}>
      <thead className="text-left text-muted">
        <tr>
          <th className="pb-1 pr-2">Branch</th>
          <th className="pb-1 pr-2">State</th>
          <th className="pb-1 pr-2">Detail</th>
        </tr>
      </thead>
      <tbody>
        {items.map((i) => (
          <tr key={i.id} className="border-t border-muted/10 align-top">
            <td className="py-1 pr-2">
              <code className="text-[10px]">{i.branch}</code>
            </td>
            <td className="py-1 pr-2">
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] ${ITEM_STATE_BADGE[i.state]}`}
              >
                {i.state.replace(/_/g, " ")}
              </span>
            </td>
            <td className="py-1 pr-2">
              {i.state === "skipped_conflict" &&
                i.conflicting_files.length > 0 && (
                  <ul className="ml-4 list-disc text-[10px] text-danger">
                    {i.conflicting_files.slice(0, 5).map((f) => (
                      <li key={f}>
                        <code>{f}</code>
                      </li>
                    ))}
                    {i.conflicting_files.length > 5 && (
                      <li>+{i.conflicting_files.length - 5} more</li>
                    )}
                  </ul>
                )}
              {i.error && (
                <pre className="whitespace-pre-wrap text-[10px] text-danger">
                  {i.error}
                </pre>
              )}
              {i.resolution_run_id && (
                <a
                  href={`/projects/${projectId}/runs/${i.resolution_run_id}`}
                  className="text-[10px] text-link underline"
                >
                  resolver run
                </a>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ResolvingFooter({
  batch,
  resolving,
  onClose,
}: {
  batch: MergeBatch;
  resolving: boolean;
  onClose: () => void;
}) {
  const pending = batch.items.filter((i) => i.state === "skipped_conflict")
    .length;
  const inFlight = batch.items.some(
    (i) => i.state === "resolution_queued" || i.state === "resolution_running",
  );
  return (
    <>
      <p className="mr-auto text-xs text-muted">
        {resolving
          ? "Queueing next resolver…"
          : inFlight
            ? "Waiting for resolver run to finish…"
            : pending > 0
              ? `${pending} conflict(s) waiting`
              : "Wrapping up…"}
      </p>
      <button className="btn" onClick={onClose}>
        run in background
      </button>
    </>
  );
}

function ShipFooter({
  batch,
  shipping,
  defaultBranch,
  onShip,
  onClose,
}: {
  batch: MergeBatch;
  shipping: boolean;
  defaultBranch: string;
  onShip: () => void;
  onClose: () => void;
}) {
  const landing = batch.items.filter(
    (i) => i.state === "merged" || i.state === "resolved",
  ).length;
  return (
    <>
      <p className="mr-auto text-xs text-muted">
        Fast-forward <code>{defaultBranch}</code> by {landing} branch
        {landing === 1 ? "" : "es"} and prune them.
      </p>
      <button className="btn" onClick={onClose}>
        not yet
      </button>
      <button
        className="btn btn-primary"
        disabled={shipping}
        onClick={onShip}
      >
        {shipping ? "Shipping…" : `Ship to ${defaultBranch}`}
      </button>
    </>
  );
}

function DoneFooter({
  batch: _batch,
  defaultBranch: _defaultBranch,
  onClose,
}: {
  batch: MergeBatch;
  defaultBranch: string;
  onClose: () => void;
}) {
  return (
    <button className="btn btn-primary" onClick={onClose}>
      done
    </button>
  );
}

function FailedFooter({
  batch: _batch,
  onClose,
}: {
  batch: MergeBatch;
  onClose: () => void;
}) {
  return (
    <button className="btn" onClick={onClose}>
      close
    </button>
  );
}
