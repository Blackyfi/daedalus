import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Idea, PlanProposal, api, apiJson, updateIdea } from "../api";
import { useApp } from "../store";

interface Props {
  ideas: Idea[];
  plans?: PlanProposal[];
  projectId: string;
}

const PROMOTED_TOOLTIP =
  "This idea has been promoted to a task and can no longer be edited.";
const PROMOTED_BADGE_TOOLTIP =
  "Tasks were created from this idea via a confirmed plan.";
const PENDING_PLAN_BADGE_TOOLTIP =
  "A pending plan proposes tasks for this idea — confirm it from the plan review panel to create them.";

export default function IdeaBox({ ideas, plans, projectId }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [tags, setTags] = useState("");

  const create = useMutation({
    mutationFn: (body: { text: string; tags: string[] }) =>
      apiJson(`/api/v1/projects/${projectId}/ideas`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ideas", projectId] });
      setText("");
      setTags("");
    },
    onError: (err: any) => flash(err.message || "Idea create failed", "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/ideas/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ideas", projectId] }),
  });

  // Set of idea ids that appear in any pending plan proposal. Tasks haven't
  // been materialized yet (`archived` flips on plan-confirm), but the user
  // should still see that something is in flight for them.
  const pendingPlanIdeaIds = useMemo(() => {
    const set = new Set<string>();
    for (const plan of plans ?? []) {
      if (plan.status !== "pending") continue;
      for (const id of plan.source_idea_ids) set.add(id);
    }
    return set;
  }, [plans]);

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({
      text: text.trim(),
      tags: tags.split(",").map((s) => s.trim()).filter(Boolean),
    });
  }

  return (
    <section className="panel">
      <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">Idea Box</h2>
      <form onSubmit={submit} className="space-y-2">
        <textarea
          className="field"
          rows={3}
          placeholder="One idea per box. First line is the title; lines starting with 'acceptance:' become criteria."
          value={text}
          onChange={(e) => setText(e.target.value)}
          required
        />
        <input
          className="field"
          placeholder="tags, comma, separated"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
        />
        <button className="btn btn-primary w-full" disabled={create.isPending}>
          {create.isPending ? "Adding…" : "Add idea"}
        </button>
      </form>
      <div className="mt-4 space-y-2">
        {ideas.length === 0 && <p className="text-xs text-muted">No ideas yet.</p>}
        {ideas.map((idea) => (
          <IdeaItem
            key={idea.id}
            idea={idea}
            projectId={projectId}
            inPendingPlan={pendingPlanIdeaIds.has(idea.id)}
            onDelete={() => remove.mutate(idea.id)}
          />
        ))}
      </div>
    </section>
  );
}

interface IdeaItemProps {
  idea: Idea;
  projectId: string;
  inPendingPlan: boolean;
  onDelete: () => void;
}

function IdeaItem({ idea, projectId, inPendingPlan, onDelete }: IdeaItemProps) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(idea.text);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const promoted = idea.archived;

  const update = useMutation({
    mutationFn: (next: string) => updateIdea(idea.id, next),
    onMutate: async (next: string) => {
      await qc.cancelQueries({ queryKey: ["ideas", projectId] });
      const previous = qc.getQueryData<Idea[]>(["ideas", projectId]);
      qc.setQueryData<Idea[]>(["ideas", projectId], (old) =>
        old ? old.map((i) => (i.id === idea.id ? { ...i, text: next } : i)) : old,
      );
      return { previous };
    },
    onError: (err: any, _next, ctx) => {
      if (ctx?.previous) qc.setQueryData(["ideas", projectId], ctx.previous);
      flash(err?.message || "Idea update failed", "error");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["ideas", projectId] }),
  });

  useEffect(() => {
    if (!editing) return;
    const ta = textareaRef.current;
    if (!ta) return;
    ta.focus();
    const end = ta.value.length;
    ta.setSelectionRange(end, end);
  }, [editing]);

  function startEdit() {
    if (promoted) return;
    setDraft(idea.text);
    setEditing(true);
  }

  function exitEdit() {
    setEditing(false);
    queueMicrotask(() => editButtonRef.current?.focus());
  }

  function cancelEdit() {
    setDraft(idea.text);
    exitEdit();
  }

  function saveEdit() {
    const next = draft.trim();
    if (!next) return;
    if (next !== idea.text) update.mutate(next);
    exitEdit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      cancelEdit();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      saveEdit();
    }
  }

  const editLabelId = `idea-edit-${idea.id}`;

  const articleClass = promoted
    ? "rounded border border-border bg-panel2/60 p-2 opacity-80"
    : inPendingPlan
      ? "rounded border border-warning/60 bg-panel2 p-2"
      : "rounded border border-border bg-panel2 p-2";

  return (
    <article className={articleClass}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="space-y-2">
              <label htmlFor={editLabelId} className="sr-only">
                Edit idea
              </label>
              <textarea
                id={editLabelId}
                ref={textareaRef}
                className="field"
                rows={3}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                aria-label="Edit idea text"
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={saveEdit}
                  disabled={!draft.trim()}
                  className="btn btn-primary"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={cancelEdit}
                  className="btn"
                >
                  Cancel
                </button>
                <span
                  className="hidden text-[10px] text-muted sm:inline"
                  aria-hidden="true"
                >
                  Enter to save · Shift+Enter for newline · Esc to cancel
                </span>
              </div>
            </div>
          ) : (
            <>
              <div className="text-xs whitespace-pre-wrap break-words">{idea.text}</div>
              {(idea.tags.length > 0 || promoted || inPendingPlan) && (
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  {promoted && (
                    <span
                      className="status-pill status-done"
                      title={PROMOTED_BADGE_TOOLTIP}
                    >
                      ✓ Tasks created
                    </span>
                  )}
                  {!promoted && inPendingPlan && (
                    <span
                      className="status-pill status-needs_fixes"
                      title={PENDING_PLAN_BADGE_TOOLTIP}
                    >
                      ⏳ In pending plan
                    </span>
                  )}
                  {idea.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-1">
            {promoted ? (
              <span title={PROMOTED_TOOLTIP} className="inline-flex">
                <button
                  ref={editButtonRef}
                  type="button"
                  disabled
                  aria-disabled="true"
                  aria-label={`Edit disabled — ${PROMOTED_TOOLTIP}`}
                  className="btn-icon text-base"
                >
                  ✎
                </button>
              </span>
            ) : (
              <button
                ref={editButtonRef}
                type="button"
                onClick={startEdit}
                title="Edit"
                aria-label="Edit idea"
                className="btn-icon text-base"
              >
                ✎
              </button>
            )}
            <button
              type="button"
              onClick={onDelete}
              title="Delete"
              aria-label="Delete idea"
              className="btn-icon text-base"
            >
              ✕
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
