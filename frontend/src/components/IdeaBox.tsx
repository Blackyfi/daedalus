import { FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Idea, apiJson, api } from "../api";
import { useApp } from "../store";

interface Props {
  ideas: Idea[];
  projectId: string;
}

export default function IdeaBox({ ideas, projectId }: Props) {
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
          <article key={idea.id} className="rounded border border-border bg-panel2 p-2">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1">
                <div className="text-xs whitespace-pre-wrap">{idea.text}</div>
                {idea.tags.length > 0 && (
                  <div className="mt-1">
                    {idea.tags.map((t) => (
                      <span key={t} className="tag">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => remove.mutate(idea.id)}
                className="btn text-[10px]"
                title="Delete"
              >
                ✕
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
