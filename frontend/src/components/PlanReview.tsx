import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PlanProposal, ProposedTask, apiJson } from "../api";
import { useApp } from "../store";

interface Props {
  proposals: PlanProposal[];
  projectId: string;
}

export default function PlanReview({ proposals, projectId }: Props) {
  if (proposals.length === 0) return null;
  return (
    <section className="panel">
      <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">
        Plan Review ({proposals.length})
      </h2>
      <div className="space-y-3">
        {proposals.map((p) => (
          <PlanCard key={p.id} plan={p} projectId={projectId} />
        ))}
      </div>
    </section>
  );
}

function PlanCard({ plan, projectId }: { plan: PlanProposal; projectId: string }) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<ProposedTask[]>(plan.proposed_tasks);

  useEffect(() => setTasks(plan.proposed_tasks), [plan.id]);

  const confirm = useMutation({
    mutationFn: (body: { proposed_tasks: ProposedTask[]; archive_source_ideas: boolean }) =>
      apiJson(`/api/v1/plans/${plan.id}/confirm`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plans", projectId] });
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      qc.invalidateQueries({ queryKey: ["ideas", projectId] });
      flash("Plan confirmed", "success");
    },
    onError: (err: any) => flash(err.message || "Plan confirm failed", "error"),
  });

  const discard = useMutation({
    mutationFn: () => apiJson(`/api/v1/plans/${plan.id}/discard`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plans", projectId] });
      flash("Plan discarded", "info");
    },
  });

  function update(i: number, patch: Partial<ProposedTask>) {
    setTasks((prev) => prev.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  }

  function removeTask(i: number) {
    setTasks((prev) => prev.filter((_, idx) => idx !== i));
  }

  return (
    <article className="rounded border border-border bg-panel2 p-3">
      <header className="mb-2 flex items-center justify-between">
        <span className="text-xs text-muted">
          Drafted {new Date(plan.created_at).toLocaleString()} · {tasks.length} task(s)
        </span>
        <div className="flex gap-2">
          <button
            className="btn btn-primary"
            onClick={() => confirm.mutate({ proposed_tasks: tasks, archive_source_ideas: true })}
            disabled={confirm.isPending}
          >
            {confirm.isPending ? "Confirming…" : "Confirm all"}
          </button>
          <button
            className="btn"
            onClick={() => discard.mutate()}
            disabled={discard.isPending}
          >
            Discard
          </button>
        </div>
      </header>
      {plan.rationale && (
        <p className="mb-2 text-xs text-muted whitespace-pre-wrap">{plan.rationale}</p>
      )}
      <div className="space-y-2">
        {tasks.map((task, i) => (
          <div key={i} className="rounded border border-border bg-panel p-2">
            <div className="grid grid-cols-4 gap-2">
              <input
                className="field col-span-3"
                value={task.title}
                onChange={(e) => update(i, { title: e.target.value })}
                placeholder="Title"
              />
              <select
                className="field"
                value={task.priority || "P2"}
                onChange={(e) =>
                  update(i, { priority: e.target.value as ProposedTask["priority"] })
                }
              >
                <option>P0</option>
                <option>P1</option>
                <option>P2</option>
                <option>P3</option>
              </select>
              <textarea
                className="field col-span-4"
                rows={2}
                value={task.description || ""}
                onChange={(e) => update(i, { description: e.target.value })}
                placeholder="Description"
              />
              <textarea
                className="field col-span-4"
                rows={2}
                value={task.acceptance_criteria || ""}
                onChange={(e) => update(i, { acceptance_criteria: e.target.value })}
                placeholder="Acceptance criteria"
              />
              <input
                className="field col-span-3"
                value={task.suggested_connector || ""}
                onChange={(e) => update(i, { suggested_connector: e.target.value })}
                placeholder="Suggested connector (or empty for project default)"
              />
              <button className="btn" onClick={() => removeTask(i)} type="button">
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}
