import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  GitStatusInfo,
  Idea,
  PlanProposal,
  Project,
  Run,
  Task,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";
import { recordVisit } from "../projectVisits";
import TaskBoard from "../components/TaskBoard";
import RunPanel from "../components/RunPanel";
import IdeaBox from "../components/IdeaBox";
import PlanReview from "../components/PlanReview";
import ProjectSettings from "../components/ProjectSettings";
import GitPullBanner from "../components/GitPullBanner";
import MergeBatchModal from "../components/MergeBatchModal";
import ProjectActionBar, { ActionItem } from "../components/ProjectActionBar";

export default function ProjectPage() {
  const { projectId, runId } = useParams();
  const navigate = useNavigate();
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();

  // Pause the three poll intervals when the tab is hidden — phones drain
  // the battery polling tasks/runs/plans every 3-5 s in the background.
  const [tabVisible, setTabVisible] = useState(
    typeof document !== "undefined" ? document.visibilityState === "visible" : true,
  );
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setTabVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const project = useQuery<Project>({
    queryKey: ["project", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}`),
    enabled: !!projectId,
  });
  const tasks = useQuery<Task[]>({
    queryKey: ["tasks", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/tasks`),
    enabled: !!projectId,
    refetchInterval: tabVisible ? 5000 : false,
  });
  const ideas = useQuery<Idea[]>({
    queryKey: ["ideas", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/ideas`),
    enabled: !!projectId,
  });
  const plans = useQuery<PlanProposal[]>({
    queryKey: ["plans", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/plans?status=pending`),
    enabled: !!projectId,
    refetchInterval: tabVisible ? 5000 : false,
  });
  // Recent-runs window. Starts at the API default (50) and the user can
  // expand it from the RunPanel — the backend caps the page at 200, so
  // these are the only useful breakpoints.
  const RUNS_LIMIT_STEPS = [50, 100, 200] as const;
  const [runsLimit, setRunsLimit] = useState<number>(RUNS_LIMIT_STEPS[0]);
  const runs = useQuery<Run[]>({
    queryKey: ["runs", projectId, runsLimit],
    queryFn: () =>
      api(`/api/v1/runs/projects/${projectId}?limit=${runsLimit}`),
    enabled: !!projectId,
    refetchInterval: tabVisible ? 3000 : false,
  });
  const loadOlderRuns = () => {
    const next = RUNS_LIMIT_STEPS.find((step) => step > runsLimit);
    if (next) setRunsLimit(next);
  };
  const canLoadOlderRuns =
    runsLimit < RUNS_LIMIT_STEPS[RUNS_LIMIT_STEPS.length - 1] &&
    (runs.data?.length ?? 0) >= runsLimit;
  const connectors = useQuery<Connector[]>({
    queryKey: ["connectors"],
    queryFn: () => api("/api/v1/connectors"),
  });
  // Git status: same query key the GitPullBanner uses, so the banner and
  // the run-buttons share a single source of truth + cache entry.
  const gitStatus = useQuery<GitStatusInfo>({
    queryKey: ["git-status", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/git-status`),
    enabled: !!projectId,
    refetchInterval: 60_000,
  });
  const blockedByPull = !!gitStatus.data?.needs_pull;

  // Snapshot current task counts as the "last seen" baseline so the project
  // list can show "+N done since you last opened". Take the snapshot once
  // per project visit (first time tasks load), and don't re-take while the
  // user remains on the page — otherwise an open tab would keep zeroing
  // the delta as work completes in the background.
  const snappedRef = useRef<string | null>(null);
  useEffect(() => {
    snappedRef.current = null; // reset whenever projectId changes
  }, [projectId]);
  useEffect(() => {
    if (!projectId || !tasks.data) return;
    if (snappedRef.current === projectId) return;
    snappedRef.current = projectId;
    const counts = {
      backlog: 0,
      ready: 0,
      in_progress: 0,
      verifying: 0,
      needs_fixes: 0,
      done: 0,
      cancelled: 0,
    };
    for (const t of tasks.data) {
      counts[t.status] += 1;
    }
    recordVisit(projectId, {
      by_status: counts,
      total: tasks.data.length,
      last_activity_at: null,
      avg_cycle_seconds_7d: null,
      completed_in_window_7d: 0,
    });
  }, [projectId, tasks.data]);

  const enqueuePlan = useMutation({
    mutationFn: () => apiJson(`/api/v1/projects/${projectId}/plan`, {}),
    onSuccess: () => {
      flash("Planning run queued", "success");
      qc.invalidateQueries({ queryKey: ["plans", projectId] });
      qc.invalidateQueries({ queryKey: ["runs", projectId] });
    },
    onError: (err: any) => flash(err.message || "Plan trigger failed", "error"),
  });

  const runAll = useMutation<Run[], Error, void>({
    mutationFn: () => apiJson<Run[]>(`/api/v1/projects/${projectId}/run-all`, {}),
    onSuccess: (runs) => {
      flash(
        `Queued ${runs.length} task run${runs.length === 1 ? "" : "s"}`,
        "success",
      );
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      qc.invalidateQueries({ queryKey: ["runs", projectId] });
    },
    onError: (err) => flash(err.message || "Run all failed", "error"),
  });

  // Tasks that already have a queued/claimed/running task-run shouldn't be
  // counted as eligible — clicking Run-all again would just double-enqueue
  // them. `ready` is the trickiest state: enqueue_task flips backlog→ready
  // *before* the scheduler claims it, so a freshly-queued task sits in
  // (status=ready, run.state=queued) for a bit. Without this filter the
  // button reads "Run all (N)" forever even after every task is queued.
  const activeTaskRunIds = new Set(
    runs.data
      ?.filter(
        (r) =>
          r.kind === "task" &&
          (r.state === "queued" || r.state === "claimed" || r.state === "running"),
      )
      .map((r) => r.task_id)
      .filter((id): id is string => id !== null) ?? [],
  );
  const eligibleCount =
    tasks.data?.filter(
      (t) =>
        (t.status === "backlog" ||
          t.status === "ready" ||
          t.status === "needs_fixes") &&
        !activeTaskRunIds.has(t.id),
    ).length ?? 0;
  const needsFixCount =
    tasks.data?.filter((t) => t.status === "needs_fixes").length ?? 0;
  const doneCount = tasks.data?.filter((t) => t.status === "done").length ?? 0;
  const [mergeOpen, setMergeOpen] = useState(false);

  // How many of the done tasks actually have something to merge? We pre-flight
  // them server-side via the merge-batch preview endpoint. The button label
  // reflects this actionable count so it's not misleading when every done
  // task is already in main.
  const mergePreflight = useQuery<{ plans: Array<{ category: string }> }>({
    queryKey: ["merge-actionable", projectId, doneCount],
    queryFn: () =>
      apiJson<{ plans: Array<{ category: string }> }>(
        `/api/v1/projects/${projectId}/merge-batch/preview`,
        { require_argus_pass: false },
      ),
    enabled: !!projectId && doneCount > 0 && !mergeOpen,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
  const actionableMergeCount =
    mergePreflight.data?.plans.filter(
      (p) => p.category === "clean" || p.category === "conflict",
    ).length ?? 0;

  // Persisted merge batches — surfaced in the action bar so awaiting-review
  // and resolving-conflict batches don't disappear behind the modal once
  // it's closed.
  type MergeBatchSummary = { id: string; state: string };
  const mergeBatches = useQuery<MergeBatchSummary[]>({
    queryKey: ["merge-batches", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/merge-batches`),
    enabled: !!projectId,
    refetchInterval: tabVisible ? 5000 : false,
  });
  const reviewableBatches =
    mergeBatches.data?.filter((b) => b.state === "awaiting_review") ?? [];
  const resolvingBatches =
    mergeBatches.data?.filter((b) => b.state === "resolving") ?? [];

  // Smooth-scroll-with-highlight: clicking a "review proposals" or "needs
  // fixes" tile lands the user precisely on the section AND flashes a brief
  // ring so their eye notices it.
  const planReviewRef = useRef<HTMLDivElement | null>(null);
  const taskBoardRef = useRef<HTMLDivElement | null>(null);
  const [pulseTarget, setPulseTarget] = useState<"plans" | "tasks" | null>(null);
  const [mergeInitialBatchId, setMergeInitialBatchId] = useState<string | null>(null);
  function flashAt(target: "plans" | "tasks") {
    const el = (target === "plans" ? planReviewRef : taskBoardRef).current;
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    setPulseTarget(target);
    window.setTimeout(() => setPulseTarget(null), 1600);
  }

  function elapsed(iso: string | null | undefined): string {
    if (!iso) return "";
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return "now";
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m`;
    return `${Math.floor(min / 60)}h ${min % 60}m`;
  }

  function confirmRunAll() {
    if (eligibleCount === 0) {
      flash("No tasks in backlog, ready, or needs-fix", "info");
      return;
    }
    const fixSuffix =
      needsFixCount > 0
        ? ` (including ${needsFixCount} needs-fix re-run${needsFixCount === 1 ? "" : "s"})`
        : "";
    if (
      window.confirm(
        `Queue ${eligibleCount} task${eligibleCount === 1 ? "" : "s"} for execution${fixSuffix}?\n\n` +
          "Tasks run per-project up to MAX_CONCURRENT_PROJECTS in parallel. " +
          "Each task will spend Claude subscription quota.",
      )
    ) {
      runAll.mutate();
    }
  }

  const runTask = useMutation({
    mutationFn: (taskId: string) =>
      apiJson(`/api/v1/tasks/${taskId}/run`, {}, { method: "POST" }),
    onSuccess: (run: any) => {
      qc.invalidateQueries({ queryKey: ["runs", projectId] });
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      navigate(`/projects/${projectId}/runs/${run.id}`);
    },
    onError: (err: any) => flash(err.message || "Could not enqueue task", "error"),
  });

  if (!project.data) {
    return <p className="text-muted">Loading project…</p>;
  }

  const activeRun = runId
    ? runs.data?.find((r) => r.id === runId) ?? null
    : runs.data?.find((r) => ["queued", "claimed", "running"].includes(r.state)) ?? null;

  // Build the action-bar items in priority order. Each entry is only added
  // when its trigger is non-empty so the bar disappears entirely on quiet
  // projects (no chrome when nothing is going on).
  const actionItems: ActionItem[] = [];
  // Most-actionable in-flight signal first.
  const liveRun = runs.data?.find((r) =>
    ["queued", "claimed", "running"].includes(r.state),
  );
  if (liveRun) {
    const liveTask =
      liveRun.task_id != null
        ? tasks.data?.find((t) => t.id === liveRun.task_id)
        : null;
    const label =
      liveRun.kind === "task" && liveTask
        ? liveTask.title
        : liveRun.kind === "argus"
          ? "Verifying"
          : liveRun.kind === "planning"
            ? "Planning"
            : liveRun.kind;
    const startedAt = liveRun.started_at;
    actionItems.push({
      key: "live-run",
      icon: (
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
      ),
      count: undefined,
      label: (
        <span>
          <span className="max-w-[18ch] truncate align-bottom inline-block">
            {label}
          </span>
          <span className="ml-1 text-[10px] opacity-70">· {elapsed(startedAt)}</span>
        </span>
      ),
      tone: liveRun.state === "running" ? "info" : "success",
      onClick: () => navigate(`/projects/${projectId}/runs/${liveRun.id}`),
      title:
        liveRun.state === "queued"
          ? "Queued — waiting to claim"
          : `Running ${liveRun.kind}`,
    });
  }
  if ((plans.data?.length ?? 0) > 0) {
    actionItems.push({
      key: "proposals",
      icon: "📋",
      count: plans.data!.length,
      label: `proposal${plans.data!.length === 1 ? "" : "s"} to review`,
      tone: "warn",
      onClick: () => flashAt("plans"),
      title: "Review and confirm proposed tasks from a planning run",
    });
  }
  if (needsFixCount > 0) {
    actionItems.push({
      key: "needs-fixes",
      icon: "⚠",
      count: needsFixCount,
      label: `need${needsFixCount === 1 ? "s" : ""} fixes`,
      tone: "warn",
      onClick: () => flashAt("tasks"),
      title: "Tasks whose last verification failed — re-run or edit",
    });
  }
  if (reviewableBatches.length > 0) {
    actionItems.push({
      key: "merge-review",
      icon: "🔀",
      count: reviewableBatches.length,
      label: `merge${reviewableBatches.length === 1 ? "" : "s"} ready to ship`,
      tone: "success",
      onClick: () => {
        setMergeInitialBatchId(reviewableBatches[0].id);
        setMergeOpen(true);
      },
      title: "Open the merge batch to review and ship",
    });
  }
  if (resolvingBatches.length > 0) {
    actionItems.push({
      key: "merge-resolving",
      icon: "🛠",
      count: resolvingBatches.length,
      label: `merge${resolvingBatches.length === 1 ? "" : "s"} resolving`,
      tone: "info",
      onClick: () => {
        setMergeInitialBatchId(resolvingBatches[0].id);
        setMergeOpen(true);
      },
      title: "Conflict resolution in progress — open to see status",
    });
  }
  // Connector rate-limit pause: surfaced from /api/v1/projects/{pid} which
  // reads the Redis key. Highest-priority signal — if visible, runs aren't
  // moving until the timestamp passes.
  if (project.data?.rate_limit_paused_until) {
    const until = new Date(project.data.rate_limit_paused_until);
    const minsLeft = Math.max(
      0,
      Math.ceil((until.getTime() - Date.now()) / 60_000),
    );
    actionItems.unshift({
      key: "rate-limit",
      icon: "⏸",
      label: (
        <span>
          Paused — Claude rate limit · resumes{" "}
          <span className="font-semibold">
            {until.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
          <span className="ml-1 text-[10px] opacity-70">
            (~{minsLeft}m)
          </span>
        </span>
      ),
      tone: "info",
      onClick: () => {
        // No-op click: this is informational. Could open a "what is this?"
        // modal in v2.
      },
      title:
        project.data.rate_limit_paused_reason ??
        "Connector hit a rate limit; queued runs will resume automatically when the window resets",
    });
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-12 lg:gap-6">
      <header className="panel flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between lg:col-span-12">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold">{project.data.name}</h1>
          <p className="text-xs text-muted">
            <span className="block truncate sm:inline">
              {project.data.workspace_path}
            </span>
            <span className="hidden sm:inline"> · </span>
            <span className="block sm:inline">
              default connector: {project.data.default_connector_id || "—"}
            </span>
            <span className="hidden sm:inline"> · </span>
            <span className="block sm:inline">
              max-fix-loops: {project.data.max_fix_loops}
            </span>
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <button
            className="btn btn-warning"
            onClick={() => enqueuePlan.mutate()}
            disabled={enqueuePlan.isPending}
          >
            {enqueuePlan.isPending ? "Queueing…" : "Plan from ideas"}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => setMergeOpen(true)}
            disabled={
              doneCount === 0 ||
              (!mergePreflight.isLoading && actionableMergeCount === 0)
            }
            title={
              doneCount === 0
                ? "No done tasks"
                : mergePreflight.isLoading
                  ? "Pre-flighting…"
                  : actionableMergeCount === 0
                    ? `All ${doneCount} done task${doneCount === 1 ? " is" : "s are"} already merged into ${project.data?.git_default_branch ?? "main"} (or empty)`
                    : `Merge ${actionableMergeCount} done task${actionableMergeCount === 1 ? "" : "s"} into an integration branch, then ship to ${project.data?.git_default_branch ?? "main"}`
            }
          >
            {mergePreflight.isLoading
              ? "Ship done tasks (…)"
              : actionableMergeCount === 0
                ? "Ship done tasks"
                : `Ship ${actionableMergeCount} done task${actionableMergeCount === 1 ? "" : "s"}`}
          </button>
          <button
            className="btn btn-primary"
            onClick={confirmRunAll}
            disabled={runAll.isPending || eligibleCount === 0 || blockedByPull}
            title={
              blockedByPull
                ? `Blocked: workspace is ${gitStatus.data?.behind_count} commit(s) behind upstream — git pull first`
                : eligibleCount === 0
                  ? activeTaskRunIds.size > 0
                    ? `All runnable tasks already queued (${activeTaskRunIds.size} in flight)`
                    : "Nothing in backlog, ready, or needs-fix"
                  : `Run ${eligibleCount} eligible task${eligibleCount === 1 ? "" : "s"}`
            }
          >
            {blockedByPull
              ? "Run all (pull required)"
              : runAll.isPending
                ? "Queueing…"
                : `Run all (${eligibleCount})`}
          </button>
        </div>
      </header>

      {projectId && (
        <div className="lg:col-span-12">
          <GitPullBanner projectId={projectId} />
        </div>
      )}

      {actionItems.length > 0 && (
        <div className="lg:col-span-12">
          <ProjectActionBar items={actionItems} />
        </div>
      )}

      <section className="space-y-4 lg:col-span-8 lg:space-y-6">
        <div
          ref={taskBoardRef}
          className={
            "rounded-md transition-shadow " +
            (pulseTarget === "tasks"
              ? "ring-2 ring-warning/70 ring-offset-2 ring-offset-bg"
              : "")
          }
        >
          <TaskBoard
            tasks={tasks.data ?? []}
            onRunTask={(id) => runTask.mutate(id)}
            connectors={connectors.data ?? []}
            projectId={projectId!}
            runDisabledReason={
              blockedByPull
                ? `Workspace is ${gitStatus.data?.behind_count} commit(s) behind ${
                    gitStatus.data?.upstream ?? "upstream"
                  } — git pull first`
                : null
            }
          />
        </div>
        <div
          ref={planReviewRef}
          className={
            "rounded-md transition-shadow " +
            (pulseTarget === "plans"
              ? "ring-2 ring-warning/70 ring-offset-2 ring-offset-bg"
              : "")
          }
        >
          <PlanReview proposals={plans.data ?? []} projectId={projectId!} />
        </div>
        <RunPanel
          runs={runs.data ?? []}
          activeRun={activeRun}
          projectId={projectId!}
          onLoadOlder={loadOlderRuns}
          canLoadOlder={canLoadOlderRuns}
          loadingOlder={runs.isFetching && (runs.data?.length ?? 0) > 0}
        />
      </section>

      <aside className="space-y-4 lg:col-span-4 lg:space-y-6">
        <IdeaBox
          ideas={ideas.data ?? []}
          plans={plans.data ?? []}
          projectId={projectId!}
        />
        <ProjectSettings project={project.data} connectors={connectors.data ?? []} />
      </aside>

      {projectId && (
        <MergeBatchModal
          open={mergeOpen}
          onClose={() => {
            setMergeOpen(false);
            setMergeInitialBatchId(null);
          }}
          projectId={projectId}
          initialBatchId={mergeInitialBatchId}
        />
      )}
    </div>
  );
}
