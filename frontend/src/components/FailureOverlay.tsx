import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArgusReport, Run, api } from "../api";

interface Props {
  run: Run;
  onClose: () => void;
}

// Strip ANSI escape sequences and bracketed paste / cursor-control noise so
// the tail-of-transcript snippet is readable. The transcript is the raw PTY
// stream, so it is full of `\x1b[...m`, `\x1b[?...h`, OSC sequences, etc.
function stripAnsi(input: string): string {
  return input
    .replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, "") // OSC … BEL / ST
    .replace(/\x1b[PX^_][^\x1b]*\x1b\\/g, "") // DCS / SOS / PM / APC
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "") // CSI …
    .replace(/\x1b[()][A-Z0-9]/g, "") // charset selectors
    .replace(/\x1b[=>78cDEHM]/g, "") // single-char escapes
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, ""); // bare control chars
}

function tail(text: string, maxLines = 80, maxChars = 8000): string {
  const cleaned = stripAnsi(text).replace(/\r\n?/g, "\n");
  const lines = cleaned.split("\n");
  // Drop trailing blank lines so the snippet ends on something meaningful.
  while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
  let snippet = lines.slice(-maxLines).join("\n");
  if (snippet.length > maxChars) snippet = snippet.slice(-maxChars);
  return snippet;
}

interface Explanation {
  headline: string;
  detail: string;
}

function explain(run: Run): Explanation {
  const ec = run.exit_code;
  switch (run.state) {
    case "aborted_unsafe":
      if (ec === -1) {
        return {
          headline: "Run was aborted in an unsafe state.",
          detail:
            "The execution lock vanished before completion. Common causes: the worker process (talos / argus-worker) crashed or restarted, the project lease expired, the wall-clock timeout was hit, or the worktree directory was un-writable (look for `talos.worktree_failed` in worker logs — frequently a permissions issue on /workspaces/<project>/runs).",
        };
      }
      return {
        headline: "Run aborted unsafely.",
        detail:
          "Hermes flagged this run as orphaned or otherwise terminated outside the normal completion path. The transcript tail below usually shows what the worker last wrote.",
      };
    case "failed": {
      if (ec === 1) {
        return {
          headline: "Process exited with status 1.",
          detail:
            "Generic failure — the connector command or one of the steps inside it returned non-zero. For task runs this is most often a CLI launch error (PATH / install issue) or a verification step (lint, test) that found problems.",
        };
      }
      if (ec === 143) {
        return {
          headline: "Process was killed (SIGTERM, exit 143).",
          detail:
            "Either a manual kill from the UI or the wall-clock timeout fired. If it ran for a long time before this code, look for the timeout in the connector spec.",
        };
      }
      if (ec === 137) {
        return {
          headline: "Process was killed (SIGKILL, exit 137).",
          detail:
            "Hard kill — usually OOM, cgroup limit, or `docker kill`.",
        };
      }
      if (ec === 422) {
        return {
          headline: "HTTP 422 from upstream.",
          detail:
            "The most common source is the LLM gateway (LiteLLM / vLLM) rejecting a request — bad model id, oversized prompt, or a schema mismatch. Check LLM_BASE_URL / LLM_MODEL.",
        };
      }
      if (ec === 124) {
        return {
          headline: "Command timed out (exit 124).",
          detail:
            "GNU `timeout` returned 124 — a step inside the connector exceeded its own timeout (separate from the Hermes wall-clock).",
        };
      }
      if (ec === null || ec === undefined) {
        return {
          headline: "Run failed without an exit code.",
          detail:
            "The runner reported failure but never captured a process exit. Usually means the failure happened before exec (e.g. worktree creation) or after the PTY had already closed.",
        };
      }
      return {
        headline: `Process exited ${ec}.`,
        detail: "Connector-specific exit code — see the transcript tail.",
      };
    }
    case "cancelled":
      return {
        headline: "Run was cancelled.",
        detail:
          "Stopped via the UI (kill / cancel). Nothing went technically wrong — the run just didn't finish.",
      };
    default:
      return {
        headline: `State: ${run.state}.`,
        detail: "No specific failure interpretation for this state.",
      };
  }
}

export default function FailureOverlay({ run, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  // Close on Esc.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const transcript = useQuery<string>({
    queryKey: ["failure-transcript", run.id],
    queryFn: () => api<string>(`/api/v1/runs/${run.id}/transcript/text`),
    retry: false,
    refetchOnWindowFocus: false,
  });

  // Argus runs sometimes have a structured verdict — surface its summary if
  // present, since for `argus` failures it is more useful than the transcript.
  const argus = useQuery<ArgusReport | null>({
    queryKey: ["failure-argus", run.id],
    queryFn: async () => {
      try {
        return await api<ArgusReport>(`/api/v1/runs/${run.id}/argus`);
      } catch {
        return null;
      }
    },
    enabled: run.kind === "argus",
    refetchOnWindowFocus: false,
  });

  const exp = explain(run);
  const snippet =
    transcript.data && transcript.data.length > 0
      ? tail(transcript.data)
      : null;

  async function copyAll() {
    const blob = [
      `run ${run.id}`,
      `kind=${run.kind} state=${run.state} exit_code=${run.exit_code ?? "n/a"}`,
      `started=${run.started_at ?? "n/a"} finished=${run.finished_at ?? "n/a"}`,
      "",
      exp.headline,
      exp.detail,
      "",
      "--- transcript tail ---",
      snippet ?? "(no transcript)",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(blob);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable in this context — silent */
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="panel w-full max-w-3xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm uppercase tracking-wide text-muted">
              Why this run failed
            </h2>
            <p className="text-xs text-muted mt-0.5 font-mono">
              {run.kind} · {run.id.slice(0, 8)} ·{" "}
              <span className={`status-pill status-${run.state}`}>
                {run.state}
              </span>
              {run.exit_code !== null && run.exit_code !== undefined && (
                <span className="ml-1">exit={run.exit_code}</span>
              )}
            </p>
          </div>
          <div className="flex gap-2">
            <button className="btn" onClick={copyAll}>
              {copied ? "copied" : "copy"}
            </button>
            <button className="btn" onClick={onClose}>
              close
            </button>
          </div>
        </header>

        <div className="space-y-3 overflow-auto pr-1">
          <section className="rounded border border-border bg-panel2 p-3">
            <h3 className="text-xs uppercase tracking-wide text-muted mb-1">
              Likely cause
            </h3>
            <p className="text-sm font-medium">{exp.headline}</p>
            <p className="text-xs text-muted mt-1 leading-relaxed">
              {exp.detail}
            </p>
          </section>

          {argus.data && (
            <section className="rounded border border-border bg-panel2 p-3">
              <h3 className="text-xs uppercase tracking-wide text-muted mb-1">
                Argus verdict ({argus.data.verdict})
              </h3>
              <p className="text-sm">{argus.data.summary}</p>
              {argus.data.findings.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs">
                  {argus.data.findings.map((f, i) => (
                    <li
                      key={i}
                      className="rounded border border-border bg-panel p-2"
                    >
                      <span className="tag">{f.severity}</span>
                      <span className="tag">{f.category}</span>
                      <span>{f.description}</span>
                      {f.evidence && (
                        <pre className="mt-1 overflow-x-auto text-[10px] text-muted">
                          {f.evidence}
                        </pre>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="rounded border border-border bg-panel2 p-3">
            <h3 className="text-xs uppercase tracking-wide text-muted mb-1">
              Transcript tail
            </h3>
            {transcript.isLoading && (
              <p className="text-xs text-muted">loading transcript…</p>
            )}
            {transcript.isError && (
              <p className="text-xs text-muted">
                Transcript unavailable —{" "}
                {(transcript.error as Error)?.message ?? "fetch failed"}.
              </p>
            )}
            {!transcript.isLoading && !transcript.isError && !snippet && (
              <p className="text-xs text-muted">
                No transcript was captured for this run. The failure happened
                before any output was streamed (e.g. worktree creation
                failure).
              </p>
            )}
            {snippet && (
              <pre className="max-h-[40vh] overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-muted font-mono">
                {snippet}
              </pre>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
