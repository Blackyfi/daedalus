import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiJson, NotificationPrefs, NotificationPrefsPatch } from "../api";
import { useApp } from "../store";

// The four notification kinds the dispatcher knows about. The labels are
// what the user sees; the `key` matches the `_PREF_FIELD_BY_KIND` mapping
// on the backend so toggling email_<key> / in_app_<key> hits the right
// columns on `UserNotificationPref`.
const EVENT_ROWS: { key: "task_completed" | "task_failed" | "task_needs_fixes" | "usage_threshold"; label: string; hint: string }[] = [
  {
    key: "task_completed",
    label: "Task completed",
    hint: "A task run finished successfully.",
  },
  {
    key: "task_failed",
    label: "Task failed",
    hint: "A run errored or was aborted.",
  },
  {
    key: "task_needs_fixes",
    label: "Task needs fixes",
    hint: "Argus marked a verification report as needing fixes.",
  },
  {
    key: "usage_threshold",
    label: "Usage threshold crossed",
    hint: "A project's cumulative cost crossed the threshold below.",
  },
];

const DEFAULT_PREFS: NotificationPrefs = {
  email_task_completed: true,
  email_task_failed: true,
  email_task_needs_fixes: true,
  email_usage_threshold: true,
  in_app_task_completed: true,
  in_app_task_failed: true,
  in_app_task_needs_fixes: true,
  in_app_usage_threshold: true,
  usage_threshold_micros: null,
};

function microsToDollars(v: number | null): string {
  if (v === null || v === undefined) return "";
  return (v / 1_000_000).toString();
}

function dollarsToMicros(s: string): number | null {
  const trimmed = s.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n * 1_000_000);
}

export default function AccountPage() {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();

  const prefs = useQuery<NotificationPrefs>({
    queryKey: ["notification-prefs"],
    queryFn: () => api<NotificationPrefs>("/api/v1/account/notification-prefs"),
  });

  const data = prefs.data ?? DEFAULT_PREFS;

  // Local string buffer for the threshold input so the user can type "1.5"
  // without us coercing it to micros on every keystroke. Synced from the
  // server payload whenever it changes (initial load, refetch, etc.).
  const [thresholdInput, setThresholdInput] = useState<string>(
    microsToDollars(data.usage_threshold_micros),
  );
  useEffect(() => {
    setThresholdInput(microsToDollars(data.usage_threshold_micros));
  }, [data.usage_threshold_micros]);

  const patch = useMutation({
    mutationFn: (body: NotificationPrefsPatch) =>
      apiJson<NotificationPrefs>("/api/v1/account/notification-prefs", body, {
        method: "PATCH",
      }),
    // Optimistic UI: snapshot the prior state, mutate the cache eagerly,
    // and roll back if the server rejects the patch.
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: ["notification-prefs"] });
      const previous = qc.getQueryData<NotificationPrefs>(["notification-prefs"]);
      const merged = { ...(previous ?? DEFAULT_PREFS), ...body } as NotificationPrefs;
      qc.setQueryData(["notification-prefs"], merged);
      return { previous };
    },
    onError: (err: Error, _body, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(["notification-prefs"], ctx.previous);
      }
      flash(err.message || "Failed to update notification preferences", "error");
    },
    onSuccess: (server) => {
      qc.setQueryData(["notification-prefs"], server);
    },
  });

  const testEmail = useMutation({
    mutationFn: () =>
      apiJson<{ status: string; to: string }>(
        "/api/v1/account/notification-prefs/test-email",
        {},
      ),
    onSuccess: (resp) => flash(`Test email sent to ${resp.to}`, "success"),
    onError: (err: Error) =>
      flash(err.message || "Failed to send test email", "error"),
  });

  const thresholdDollars = useMemo(
    () => microsToDollars(data.usage_threshold_micros),
    [data.usage_threshold_micros],
  );
  const thresholdDirty = thresholdInput !== thresholdDollars;

  function commitThreshold() {
    const next = dollarsToMicros(thresholdInput);
    if (next === data.usage_threshold_micros) return;
    patch.mutate({ usage_threshold_micros: next });
  }

  function clearThreshold() {
    setThresholdInput("");
    if (data.usage_threshold_micros !== null) {
      patch.mutate({ usage_threshold_micros: null });
    }
  }

  return (
    <div className="space-y-4">
      <section className="panel space-y-4">
        <h2 className="text-sm uppercase tracking-wide text-muted">Account</h2>
        <p className="text-xs text-muted">
          Per-user preferences for delivery channels and the project cost ceiling
          that triggers a usage notification.
        </p>
      </section>

      <section className="panel space-y-4">
        <header className="flex items-center justify-between">
          <h3 className="text-xs uppercase tracking-wide text-muted">
            Notifications
          </h3>
          <button
            className="btn btn-primary"
            onClick={() => testEmail.mutate()}
            disabled={testEmail.isPending}
          >
            {testEmail.isPending ? "Sending…" : "Send test email"}
          </button>
        </header>

        {prefs.isLoading && (
          <p className="text-xs text-muted">Loading preferences…</p>
        )}
        {prefs.error && (
          <p className="text-xs text-danger">
            Failed to load: {(prefs.error as Error).message}
          </p>
        )}

        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="px-2 py-1">Event</th>
              <th className="px-2 py-1 text-center">Email</th>
              <th className="px-2 py-1 text-center">In-app</th>
            </tr>
          </thead>
          <tbody>
            {EVENT_ROWS.map((row) => {
              const emailKey = `email_${row.key}` as keyof NotificationPrefs;
              const inAppKey = `in_app_${row.key}` as keyof NotificationPrefs;
              return (
                <tr key={row.key} className="border-t border-border">
                  <td className="px-2 py-2">
                    <div>{row.label}</div>
                    <div className="text-[11px] text-muted">{row.hint}</div>
                  </td>
                  <td className="px-2 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${row.label} email`}
                      checked={Boolean(data[emailKey])}
                      onChange={(e) =>
                        patch.mutate({ [emailKey]: e.target.checked } as NotificationPrefsPatch)
                      }
                      disabled={prefs.isLoading}
                    />
                  </td>
                  <td className="px-2 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${row.label} in-app`}
                      checked={Boolean(data[inAppKey])}
                      onChange={(e) =>
                        patch.mutate({ [inAppKey]: e.target.checked } as NotificationPrefsPatch)
                      }
                      disabled={prefs.isLoading}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div className="border-t border-border pt-4">
          <label className="label" htmlFor="usage-threshold-slider">
            Usage threshold (USD)
          </label>
          <p className="mb-2 text-[11px] text-muted">
            Fires <code>usage_threshold</code> once when a project's cumulative
            cost crosses this ceiling. Leave empty to disable.
          </p>
          <div className="flex items-center gap-3">
            <input
              id="usage-threshold-slider"
              type="range"
              min={0}
              max={500}
              step={1}
              value={Number(thresholdInput) || 0}
              onChange={(e) => setThresholdInput(e.target.value)}
              onMouseUp={commitThreshold}
              onTouchEnd={commitThreshold}
              onKeyUp={(e) => {
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") commitThreshold();
              }}
              className="flex-1"
              aria-label="Usage threshold slider"
            />
            <input
              type="number"
              min={0}
              step="0.01"
              value={thresholdInput}
              onChange={(e) => setThresholdInput(e.target.value)}
              onBlur={commitThreshold}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitThreshold();
                }
              }}
              className="field w-32"
              placeholder="—"
              aria-label="Usage threshold in dollars"
            />
            <button
              className="btn"
              onClick={clearThreshold}
              disabled={data.usage_threshold_micros === null && !thresholdInput}
            >
              Clear
            </button>
          </div>
          {thresholdDirty && (
            <div className="mt-2 text-[11px] text-muted">
              Press Enter or click outside to save.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
