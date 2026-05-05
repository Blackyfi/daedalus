import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { ArgusReport, Run, Snapshot, api, apiJson } from "../api";
import { useApp } from "../store";
import {
  reportLiveRunnerEmpty,
  reportPtyWebSocketError,
  reportTranscriptFetchFailed,
} from "../diagnostics";
import DiffViewer from "./DiffViewer";

interface Props {
  runs: Run[];
  activeRun: Run | null;
  projectId: string;
}

export default function RunPanel({ runs, activeRun, projectId }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const heldInputRef = useRef<boolean>(false);
  const lastHoldStateRef = useRef<boolean | null>(null);
  // Bytes written into the xterm since this run was attached. Drives the
  // watchdog that fires `live_runner_empty` if nothing renders for a run
  // that *should* have content.
  const bytesReceivedRef = useRef<number>(0);
  const [transcriptText, setTranscriptText] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);
  const [diffText, setDiffText] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [holdsInput, setHoldsInput] = useState<boolean>(false);
  const [heldBy, setHeldBy] = useState<string | null>(null);

  // Set up xterm once.
  useEffect(() => {
    if (!containerRef.current || termRef.current) return;
    const term = new Terminal({
      fontFamily: '"JetBrains Mono", "SF Mono", Menlo, monospace',
      fontSize: 12,
      theme: { background: "#0a0e14", foreground: "#e6edf3" },
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;
    const onResize = () => {
      fit.fit();
      const { rows, cols } = term;
      if (activeRun) {
        apiJson(`/api/v1/runs/${activeRun.id}/resize`, { rows, cols }).catch(() => {});
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      term.dispose();
      termRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Attach to whichever run is active.
  useEffect(() => {
    if (!termRef.current) return;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setTranscriptText(null);
    setShowTranscript(false);
    setDiffText(null);
    setShowDiff(false);
    setHoldsInput(false);
    setHeldBy(null);
    heldInputRef.current = false;
    lastHoldStateRef.current = null;
    bytesReceivedRef.current = 0;
    termRef.current.clear();
    if (!activeRun) return;

    // Replay persisted transcript into the terminal first. The live PTY stream
    // only carries new bytes; without this the xterm is empty for any run
    // that's already finished (or that was attached to after some output had
    // already streamed past). Live deltas write on top via the WebSocket.
    let cancelled = false;
    const TERMINAL_STATES = new Set([
      "completed",
      "failed",
      "cancelled",
      "aborted_unsafe",
    ]);
    if (TERMINAL_STATES.has(activeRun.state)) {
      api<string>(`/api/v1/runs/${activeRun.id}/transcript/text`)
        .then((text) => {
          if (cancelled) return;
          if (typeof text === "string" && text.length > 0) {
            termRef.current?.write(text);
            bytesReceivedRef.current += text.length;
          } else {
            termRef.current?.write(
              "\x1b[2m(no transcript captured for this run)\x1b[22m\r\n"
            );
          }
        })
        .catch((err) => {
          if (cancelled) return;
          termRef.current?.write(
            "\x1b[2m(transcript not yet available)\x1b[22m\r\n"
          );
          reportTranscriptFetchFailed(
            activeRun.id,
            (err && err.message) || "transcript fetch failed",
          );
        });
    }

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/pty/${activeRun.id}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      let msg: any;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.t === "data") {
        const d = typeof msg.d === "string" ? msg.d : "";
        if (d.length > 0) {
          bytesReceivedRef.current += d.length;
          termRef.current?.write(d);
        }
      } else if (msg.t === "state") {
        const newHold = !!msg.you_hold_input;
        const previous = lastHoldStateRef.current;
        if (previous === true && newHold === false) {
          flash(
            msg.held_by
              ? `Input handed to ${msg.held_by}`
              : "You no longer hold input",
            "info"
          );
        } else if (previous === false && newHold === true) {
          flash("You now hold input on this run", "success");
        }
        heldInputRef.current = newHold;
        lastHoldStateRef.current = newHold;
        setHoldsInput(newHold);
        setHeldBy(typeof msg.held_by === "string" ? msg.held_by : null);
      }
    };

    ws.onerror = () => {
      reportPtyWebSocketError(activeRun.id, "error", "PTY WebSocket reported an error");
    };
    ws.onclose = (e) => {
      // If the socket closed before any bytes ever arrived, surface it —
      // that's the "I clicked the run and the terminal stayed blank" case.
      if (bytesReceivedRef.current === 0) {
        reportPtyWebSocketError(
          activeRun.id,
          "closed_early",
          `closed code=${e.code} reason=${e.reason || "(none)"}`,
        );
      }
    };

    // Watchdog — if 10s pass and we've rendered zero bytes for a run that
    // should have output (running or already-completed), file a diagnostic
    // so the issue is in the audit log instead of dying silently.
    const wantsContent =
      activeRun.state === "running" ||
      TERMINAL_STATES.has(activeRun.state);
    const watchdogStartMs = Date.now();
    const watchdog = window.setTimeout(() => {
      if (cancelled) return;
      if (bytesReceivedRef.current === 0 && wantsContent) {
        reportLiveRunnerEmpty(
          activeRun.id,
          activeRun.state,
          Date.now() - watchdogStartMs,
        );
      }
    }, 10_000);

    const inputDispose = termRef.current.onData((data) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (!heldInputRef.current) return;
      ws.send(JSON.stringify({ t: "input", d: data }));
    });

    // Holder heartbeat — keeps the Redis TTL alive so the role doesn't fall
    // back to vacant just because the user paused typing.
    const heartbeat = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN && heldInputRef.current) {
        ws.send(JSON.stringify({ t: "ping" }));
      }
    }, 30_000);

    // Send initial size once attached.
    ws.onopen = () => {
      const term = termRef.current!;
      apiJson(`/api/v1/runs/${activeRun.id}/resize`, {
        rows: term.rows,
        cols: term.cols,
      }).catch(() => {});
    };
    return () => {
      cancelled = true;
      window.clearTimeout(watchdog);
      inputDispose.dispose();
      window.clearInterval(heartbeat);
      ws.close();
    };
  }, [activeRun, flash]);

  function takeOverInput() {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ t: "takeover" }));
  }

  function releaseInput() {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ t: "release" }));
  }

  const argus = useQuery<ArgusReport | null>({
    queryKey: ["argus", activeRun?.id],
    queryFn: async () => {
      if (!activeRun || activeRun.kind !== "argus") return null;
      try {
        return await api(`/api/v1/runs/${activeRun.id}/argus`);
      } catch {
        return null;
      }
    },
    enabled: !!activeRun && activeRun.kind === "argus",
  });

  const snapshot = useQuery<Snapshot | null>({
    queryKey: ["snapshot", activeRun?.id],
    queryFn: async () => {
      if (!activeRun) return null;
      try {
        return await api(`/api/v1/runs/${activeRun.id}/snapshot`);
      } catch {
        return null;
      }
    },
    // Only task runs ever produce snapshots (yolo profile creates a git
    // tag pre-flight). Planning / argus / cleanup runs never have one,
    // so don't bother asking — it just generates 404 noise.
    enabled: !!activeRun && activeRun.kind === "task",
  });

  const lifecycle = useMutation({
    mutationFn: ({ rid, action }: { rid: string; action: string }) =>
      apiJson(`/api/v1/runs/${rid}/${action}`, {}),
    onSuccess: (_, vars) => flash(`Sent ${vars.action} to ${vars.rid}`, "success"),
    onError: (err: any) => flash(err.message || "Action failed", "error"),
  });

  const rollback = useMutation({
    mutationFn: (rid: string) => apiJson(`/api/v1/runs/${rid}/rollback`, {}),
    onSuccess: () => flash("Worktree rolled back to snapshot", "success"),
    onError: (err: any) => flash(err.message || "Rollback failed", "error"),
  });

  const retry = useMutation<Run, Error, string>({
    mutationFn: (rid) => apiJson<Run>(`/api/v1/runs/${rid}/retry`, {}),
    onSuccess: (newRun) => {
      flash("Run re-queued", "success");
      qc.invalidateQueries({ queryKey: ["runs", projectId] });
      qc.invalidateQueries({ queryKey: ["tasks", projectId] });
      navigate(`/projects/${projectId}/runs/${newRun.id}`);
    },
    onError: (err) => flash(err.message || "Retry failed", "error"),
  });

  const FAILED_STATES = new Set(["failed", "cancelled", "aborted_unsafe"]);

  async function loadTranscript(runId: string) {
    try {
      const text = await api<string>(`/api/v1/runs/${runId}/transcript/text`);
      setTranscriptText(typeof text === "string" ? text : JSON.stringify(text));
      setShowTranscript(true);
    } catch (err: any) {
      flash(err.message || "Transcript unavailable", "error");
    }
  }

  async function loadDiff(runId: string) {
    try {
      const text = await api<string>(`/api/v1/runs/${runId}/diff`);
      setDiffText(typeof text === "string" ? text : JSON.stringify(text));
      setShowDiff(true);
    } catch (err: any) {
      flash(err.message || "Diff unavailable", "error");
    }
  }

  const usageLabel = activeRun
    ? formatUsage(activeRun.token_input, activeRun.token_output, activeRun.cost_usd_micros)
    : null;

  return (
    <section className="panel">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-sm uppercase tracking-wide text-muted">Live runner</h2>
        <div className="flex items-center gap-3 text-xs text-muted">
          {usageLabel && <span>{usageLabel}</span>}
          <span>
            {activeRun
              ? `${activeRun.kind} · ${activeRun.id.slice(0, 8)} · ${activeRun.state}`
              : "no active run"}
          </span>
        </div>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <div
            ref={containerRef}
            className="h-[420px] w-full rounded border border-border bg-bg p-2"
          />
          {activeRun && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span
                className={`status-pill ${
                  holdsInput ? "status-done" : "status-needs_fixes"
                }`}
                title={
                  holdsInput
                    ? "You're the input holder — keystrokes go to the agent."
                    : heldBy
                      ? `Read-only. Input held by ${heldBy}.`
                      : "Read-only. No one is holding input."
                }
              >
                {holdsInput
                  ? "Input: you"
                  : heldBy
                    ? `Input: ${heldBy}`
                    : "Input: vacant"}
              </span>
              {!holdsInput && (
                <button className="btn" onClick={takeOverInput}>
                  Take input
                </button>
              )}
              {holdsInput && (
                <button className="btn" onClick={releaseInput}>
                  Release input
                </button>
              )}
              {(["pause", "resume", "interrupt", "kill", "detach"] as const).map((a) => (
                <button
                  key={a}
                  className="btn"
                  onClick={() => lifecycle.mutate({ rid: activeRun.id, action: a })}
                >
                  {a}
                </button>
              ))}
              <button className="btn" onClick={() => loadTranscript(activeRun.id)}>
                transcript
              </button>
              <button className="btn" onClick={() => loadDiff(activeRun.id)}>
                diff
              </button>
              {snapshot.data?.git_tag && (
                <button
                  className="btn btn-warning"
                  onClick={() => rollback.mutate(activeRun.id)}
                >
                  Rollback ({snapshot.data.git_tag})
                </button>
              )}
              {FAILED_STATES.has(activeRun.state) && (
                <button
                  className="btn btn-primary"
                  onClick={() => retry.mutate(activeRun.id)}
                  disabled={retry.isPending}
                >
                  {retry.isPending ? "Retrying…" : "Retry"}
                </button>
              )}
            </div>
          )}
        </div>

        <aside className="space-y-2">
          <h3 className="text-xs uppercase tracking-wide text-muted">Recent runs</h3>
          <div className="space-y-1">
            {runs.slice(0, 12).map((r) => (
              <div
                key={r.id}
                className={`rounded border border-border p-2 text-xs ${
                  activeRun?.id === r.id ? "border-accent bg-panel2" : "bg-panel"
                }`}
              >
                <button
                  onClick={() => navigate(`/projects/${projectId}/runs/${r.id}`)}
                  className="block w-full text-left hover:text-accent"
                >
                  <div className="flex items-center justify-between">
                    <span>
                      {r.kind} · {r.id.slice(0, 8)}
                      {r.retry_of && <span className="ml-1 text-muted">↻</span>}
                    </span>
                    <span className={`status-pill status-${r.state}`}>{r.state}</span>
                  </div>
                  <div className="text-[10px] text-muted mt-0.5">
                    {r.started_at
                      ? new Date(r.started_at).toLocaleString()
                      : "not started"}
                  </div>
                </button>
                {FAILED_STATES.has(r.state) && (
                  <button
                    className="btn mt-1 w-full text-[10px]"
                    onClick={() => retry.mutate(r.id)}
                    disabled={retry.isPending}
                  >
                    {retry.isPending ? "Retrying…" : "Retry"}
                  </button>
                )}
              </div>
            ))}
          </div>
        </aside>
      </div>

      {argus.data && (
        <div className="mt-4 panel">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-xs uppercase tracking-wide text-muted">Argus verdict</h3>
            <span className={`status-pill status-${argus.data.verdict === "pass" ? "done" : argus.data.verdict === "fail" ? "failed" : "needs_fixes"}`}>
              {argus.data.verdict}
            </span>
          </header>
          <p className="text-sm">{argus.data.summary}</p>
          {argus.data.findings.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {argus.data.findings.map((f, i) => (
                <li key={i} className="rounded border border-border bg-panel2 p-2">
                  <span className="tag">{f.severity}</span>
                  <span className="tag">{f.category}</span>
                  <span>{f.description}</span>
                  {f.evidence && (
                    <pre className="mt-1 overflow-x-auto text-[10px] text-muted">{f.evidence}</pre>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {usageLabel && (
        <div className="mt-3 text-xs text-muted">
          Usage on this run · {usageLabel}
        </div>
      )}

      {showTranscript && transcriptText !== null && (
        <div className="mt-4 panel">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-xs uppercase tracking-wide text-muted">Transcript</h3>
            <button className="btn" onClick={() => setShowTranscript(false)}>
              close
            </button>
          </header>
          <pre className="max-h-[400px] overflow-auto text-xs text-muted whitespace-pre-wrap">
            {transcriptText}
          </pre>
        </div>
      )}

      {showDiff && diffText !== null && (
        <div className="mt-4 panel">
          <header className="mb-2 flex items-center justify-between">
            <h3 className="text-xs uppercase tracking-wide text-muted">
              Diff vs default branch
            </h3>
            <button className="btn" onClick={() => setShowDiff(false)}>
              close
            </button>
          </header>
          <DiffViewer patch={diffText} />
        </div>
      )}
    </section>
  );
}

function formatUsage(
  inputTokens: number | null,
  outputTokens: number | null,
  costMicros: number | null,
): string | null {
  const parts: string[] = [];
  if (inputTokens != null) parts.push(`${formatTokens(inputTokens)} in`);
  if (outputTokens != null) parts.push(`${formatTokens(outputTokens)} out`);
  if (costMicros != null) parts.push(formatCost(costMicros));
  return parts.length ? parts.join(" · ") : null;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(micros: number): string {
  // micros = 1e-6 USD; 1 USD = 1_000_000 micros.
  const usd = micros / 1_000_000;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(4)}`;
}
