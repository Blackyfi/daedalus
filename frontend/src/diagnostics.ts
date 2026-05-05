// SPA diagnostic reporter — POSTs structured events to /api/v1/diagnostics/log
// so issues like "live runner terminal stayed empty" surface in the audit log
// instead of dying silently in the user's devtools.
//
// Usage:
//   import { reportDiagnostic } from "./diagnostics";
//   reportDiagnostic("live_runner_empty", "no PTY data after 10s", { run_id });

const ENDPOINT = "/api/v1/diagnostics/log";

// Per-kind suppression: don't fire the same kind twice in a 60s window from
// the same tab. Stops a misbehaving page from flooding the audit log when
// the underlying problem keeps re-triggering.
const _lastFired: Map<string, number> = new Map();
const SUPPRESS_MS = 60_000;

// Per-tab dedupe: if the same {kind, run_id} was already reported in this
// session, skip. The browser will reset on reload, which is fine.
const _seen: Set<string> = new Set();

export interface DiagnosticPayload {
  run_id?: string;
  project_id?: string;
  context?: Record<string, unknown>;
}

export function reportDiagnostic(
  kind: string,
  message: string,
  extra?: DiagnosticPayload,
): void {
  const dedupeKey = `${kind}|${extra?.run_id ?? ""}`;
  if (_seen.has(dedupeKey)) return;
  const now = Date.now();
  const last = _lastFired.get(kind) ?? 0;
  if (now - last < SUPPRESS_MS) return;

  _lastFired.set(kind, now);
  _seen.add(dedupeKey);

  const body = {
    kind,
    message: (message ?? "").toString().slice(0, 1024),
    run_id: extra?.run_id ?? null,
    project_id: extra?.project_id ?? null,
    url: typeof location !== "undefined" ? location.href.slice(0, 512) : null,
    user_agent:
      typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 512) : null,
    stack: extra?.context?.stack
      ? String(extra.context.stack).slice(0, 4096)
      : null,
    context: extra?.context ?? {},
  };

  // Best-effort fire-and-forget. If the endpoint is unreachable or the user
  // is logged out, we don't want a diagnostic about a diagnostic.
  fetch(ENDPOINT, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => {
    /* swallow */
  });
}

// Convenience helpers used from the runtime watchers.
export function reportRenderError(error: Error, info: { componentStack?: string } = {}): void {
  reportDiagnostic("render_error", error.message || "render error", {
    context: {
      stack: error.stack,
      component_stack: info.componentStack,
    },
  });
}

export function reportLiveRunnerEmpty(runId: string, runState: string, elapsedMs: number): void {
  reportDiagnostic(
    "live_runner_empty",
    `no PTY data rendered after ${Math.round(elapsedMs / 1000)}s`,
    {
      run_id: runId,
      context: { run_state: runState, elapsed_ms: elapsedMs },
    },
  );
}

export function reportPtyWebSocketError(runId: string, kind: "error" | "no_data" | "closed_early", message: string): void {
  reportDiagnostic(
    `pty_ws_${kind}`,
    message,
    { run_id: runId, context: { ws_state: kind } },
  );
}

export function reportTranscriptFetchFailed(runId: string, message: string): void {
  reportDiagnostic("transcript_fetch_failed", message, { run_id: runId });
}
