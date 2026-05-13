import { useEffect, useRef } from "react";
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
import AutoRunPanel from "../components/AutoRunPanel";
import GitPullBanner from "../components/GitPullBanner";

export default function ProjectPage() {
  const { projectId, runId } = useParams();
  const navigate = useNavigate();
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();

  const project = useQuery<Project>({
    queryKey: ["project", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}`),
    enabled: !!projectId,
  });
  const tasks = useQuery<Task[]>({
    queryKey: ["tasks", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/tasks`),
    enabled: !!projectId,
    refetchInterval: 5000,
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
    refetchInterval: 5000,
  });
  const runs = useQuery<Run[]>({
    queryKey: ["runs", projectId],
    queryFn: () => api(`/api/v1/runs/projects/${projectId}`),
    enabled: !!projectId,
    refetchInterval: 3000,
  });
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

  const eligibleCount =
    tasks.data?.filter(
      (t) =>
        t.status === "backlog" ||
        t.status === "ready" ||
        t.status === "needs_fixes",
    ).length ?? 0;
  const needsFixCount =
    tasks.data?.filter((t) => t.status === "needs_fixes").length ?? 0;

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

  return (
    <div className="grid grid-cols-12 gap-6">
      <header className="col-span-12 panel flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{project.data.name}</h1>
          <p className="text-xs text-muted">
            {project.data.workspace_path} · default connector:{" "}
            {project.data.default_connector_id || "—"} · max-fix-loops:{" "}
            {project.data.max_fix_loops}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-warning"
            onClick={() => enqueuePlan.mutate()}
            disabled={enqueuePlan.isPending}
          >
            {enqueuePlan.isPending ? "Queueing…" : "Plan from ideas"}
          </button>
          <button
            className="btn btn-primary"
            onClick={confirmRunAll}
            disabled={runAll.isPending || eligibleCount === 0 || blockedByPull}
            title={
              blockedByPull
                ? `Blocked: workspace is ${gitStatus.data?.behind_count} commit(s) behind upstream — git pull first`
                : eligibleCount === 0
                  ? "Nothing in backlog or ready"
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
        <div className="col-span-12">
          <GitPullBanner projectId={projectId} />
        </div>
      )}

      <section className="col-span-8 space-y-6">
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
        <PlanReview proposals={plans.data ?? []} projectId={projectId!} />
        <RunPanel runs={runs.data ?? []} activeRun={activeRun} projectId={projectId!} />
      </section>

      <aside className="col-span-4 space-y-6">
        <IdeaBox ideas={ideas.data ?? []} projectId={projectId!} />
        <AutoRunPanel project={project.data} connectors={connectors.data ?? []} />
        <ProjectSettings project={project.data} connectors={connectors.data ?? []} />
      </aside>
    </div>
  );
}
