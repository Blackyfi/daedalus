import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitStatusInfo, api } from "../api";
import { useApp } from "../store";

interface Props {
  projectId: string;
}

/** Red bar shown when the project workspace is behind its upstream.
 *
 * The bar disappears (renders null) when the workspace is up-to-date or has
 * no upstream — we don't want to nag for non-git projects or local-only repos.
 */
export default function GitPullBanner({ projectId }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const status = useQuery<GitStatusInfo>({
    queryKey: ["git-status", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/git-status?refresh=true`),
    refetchInterval: 60_000,
    // Refetch on mount with `refresh=true` so the cache reflects current
    // remote state every time the user opens the project page.
    refetchOnMount: "always",
  });

  const recheck = useMutation<GitStatusInfo, Error>({
    mutationFn: () =>
      api(`/api/v1/projects/${projectId}/git-status?refresh=true`),
    onSuccess: (data) => {
      qc.setQueryData(["git-status", projectId], data);
      qc.invalidateQueries({ queryKey: ["project-git-status"] });
      if (!data.needs_pull) {
        flash("Workspace is up to date", "success");
      }
    },
    onError: (err) => flash(err.message || "Recheck failed", "error"),
  });

  const data = status.data;
  if (!data || !data.is_git_repo || !data.has_remote) return null;

  // Fetch failure banner: amber, doesn't block enqueues.
  if (data.fetch_failed) {
    return (
      <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
        <div className="flex flex-wrap items-center gap-2">
          <strong className="uppercase tracking-wide">git fetch failed</strong>
          <span>{data.fetch_error || "remote unreachable"}</span>
          <button
            className="ml-auto btn"
            onClick={() => recheck.mutate()}
            disabled={recheck.isPending}
          >
            {recheck.isPending ? "Rechecking…" : "Recheck"}
          </button>
        </div>
        <p className="mt-1 text-[10px] opacity-80">
          Daedalus can't reach {data.upstream ?? "the upstream"} — the
          behind-count below may be stale.
        </p>
      </div>
    );
  }

  if (!data.needs_pull) return null;

  return (
    <div className="rounded border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      <div className="flex flex-wrap items-center gap-3">
        <strong className="uppercase tracking-wide text-rose-300">
          Pull required
        </strong>
        <span>
          {data.behind_count} commit{data.behind_count === 1 ? "" : "s"} behind{" "}
          <code className="rounded bg-rose-500/20 px-1">
            {data.upstream ?? "upstream"}
          </code>
          .
        </span>
        <span className="text-rose-300/80">
          Run <code className="rounded bg-rose-500/20 px-1">git pull</code> in
          the workspace before launching agent tasks.
        </span>
        <button
          className="ml-auto btn"
          onClick={() => recheck.mutate()}
          disabled={recheck.isPending}
        >
          {recheck.isPending ? "Rechecking…" : "Recheck"}
        </button>
      </div>
      <p className="mt-1 text-[11px] opacity-80">
        Branch <code>{data.branch}</code>
        {data.ahead_count > 0 && (
          <> · {data.ahead_count} local commit{data.ahead_count === 1 ? "" : "s"} ahead</>
        )}
        {data.last_fetched_at && (
          <> · last fetch {new Date(data.last_fetched_at).toLocaleTimeString()}</>
        )}
      </p>
    </div>
  );
}
