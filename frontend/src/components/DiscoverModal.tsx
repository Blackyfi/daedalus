import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Connector,
  DiscoverRepoEntry,
  DiscoveredRepo,
  Project,
  api,
  apiJson,
} from "../api";
import { useApp } from "../store";

interface Props {
  open: boolean;
  onClose: () => void;
  connectors: Connector[];
}

interface Selection {
  selected: boolean;
  name: string;
  default_connector_id: string;
}

export default function DiscoverModal({ open, onClose, connectors }: Props) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();

  const repos = useQuery<DiscoveredRepo[]>({
    queryKey: ["discover", "repos"],
    queryFn: () => api("/api/v1/discover/repos"),
    enabled: open,
    refetchOnWindowFocus: false,
  });

  const [picks, setPicks] = useState<Record<string, Selection>>({});
  const [defaultConnector, setDefaultConnector] = useState<string>("");

  // Initialise selection state every time the modal opens / data refreshes.
  useEffect(() => {
    if (!repos.data) return;
    setPicks((prev) => {
      const next: Record<string, Selection> = {};
      for (const r of repos.data!) {
        next[r.path] = prev[r.path] ?? {
          selected: !r.already_registered,
          name: r.name,
          default_connector_id: defaultConnector,
        };
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repos.data]);

  const selectedCount = useMemo(
    () => Object.values(picks).filter((p) => p.selected).length,
    [picks],
  );

  const register = useMutation<Project[], Error, DiscoverRepoEntry[]>({
    mutationFn: (entries) =>
      apiJson<Project[]>("/api/v1/discover/register", { repos: entries }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["discover", "repos"] });
      flash(
        `Registered ${created.length} project${created.length === 1 ? "" : "s"}`,
        "success",
      );
      onClose();
    },
    onError: (err) => flash(err.message || "Register failed", "error"),
  });

  function setAll(selected: boolean) {
    if (!repos.data) return;
    setPicks((prev) => {
      const next = { ...prev };
      for (const r of repos.data!) {
        if (r.already_registered) continue;
        next[r.path] = { ...next[r.path], selected };
      }
      return next;
    });
  }

  function applyDefaultConnector(connectorId: string) {
    setDefaultConnector(connectorId);
    setPicks((prev) => {
      const next: Record<string, Selection> = {};
      for (const path in prev) next[path] = { ...prev[path], default_connector_id: connectorId };
      return next;
    });
  }

  function submit() {
    const entries: DiscoverRepoEntry[] = [];
    for (const r of repos.data ?? []) {
      const p = picks[r.path];
      if (!p?.selected || r.already_registered) continue;
      entries.push({
        path: r.path,
        name: p.name.trim() || r.name,
        description: r.description || undefined,
        git_default_branch: r.default_branch,
        default_connector_id: p.default_connector_id || null,
      });
    }
    if (entries.length === 0) {
      flash("Nothing selected", "info");
      return;
    }
    register.mutate(entries);
  }

  // Close on Escape — phones cannot tap a 28 px close button reliably,
  // and the previous behaviour swallowed the keypress.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="discover-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="panel flex w-full max-h-[90vh] max-w-4xl flex-col sm:max-h-[85vh]">
        <header className="mb-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 id="discover-title" className="text-sm uppercase tracking-wide text-muted">
              Discover repos
            </h2>
            <p className="mt-0.5 hidden text-xs text-muted sm:block">
              Walks the workspaces root for git repos. Pick which to register
              as Daedalus projects.
            </p>
          </div>
          <button className="btn shrink-0" onClick={onClose} aria-label="Close">
            close
          </button>
        </header>

        {repos.isLoading && <p className="text-sm text-muted">Scanning…</p>}
        {repos.error && (
          <p className="text-sm text-danger">
            Discovery failed: {(repos.error as Error).message}
          </p>
        )}

        {repos.data && repos.data.length === 0 && (
          <p className="text-sm text-muted">
            No git repos found under the configured workspaces root.
          </p>
        )}

        {repos.data && repos.data.length > 0 && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <button className="btn" onClick={() => setAll(true)}>
                Select all
              </button>
              <button className="btn" onClick={() => setAll(false)}>
                Clear
              </button>
              <span className="text-muted">·</span>
              <label className="label !mb-0">Apply connector to all:</label>
              <select
                className="field !w-auto"
                value={defaultConnector}
                onChange={(e) => applyDefaultConnector(e.target.value)}
              >
                <option value="">(none)</option>
                {connectors.map((c) => (
                  <option key={c.connector_id} value={c.connector_id}>
                    {c.display_name}
                  </option>
                ))}
              </select>
              <span className="ml-auto text-muted">{selectedCount} selected</span>
            </div>

            <div className="-mx-2 flex-1 overflow-auto px-2">
              <table className="w-full min-w-[720px] text-xs">
                <thead className="sticky top-0 bg-panel">
                  <tr className="text-left text-muted">
                    <th className="py-1 pr-2 w-8"></th>
                    <th className="py-1 pr-2">Path</th>
                    <th className="py-1 pr-2">Name</th>
                    <th className="py-1 pr-2">Branch</th>
                    <th className="py-1 pr-2">Last commit</th>
                    <th className="py-1 pr-2">Connector</th>
                    <th className="py-1 pr-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {repos.data.map((r) => {
                    const pick = picks[r.path] ?? {
                      selected: false,
                      name: r.name,
                      default_connector_id: defaultConnector,
                    };
                    return (
                      <tr
                        key={r.path}
                        className={`border-t border-border ${
                          r.already_registered ? "opacity-50" : ""
                        }`}
                      >
                        <td className="py-1 pr-2">
                          <input
                            type="checkbox"
                            className="h-4 w-4 cursor-pointer accent-accent md:h-3.5 md:w-3.5"
                            checked={pick.selected}
                            disabled={r.already_registered}
                            onChange={(e) =>
                              setPicks((prev) => ({
                                ...prev,
                                [r.path]: { ...pick, selected: e.target.checked },
                              }))
                            }
                            aria-label={`Select ${r.relative_path}`}
                          />
                        </td>
                        <td className="py-1 pr-2 font-mono">{r.relative_path}</td>
                        <td className="py-1 pr-2">
                          <input
                            className="field !py-0.5"
                            value={pick.name}
                            disabled={r.already_registered}
                            onChange={(e) =>
                              setPicks((prev) => ({
                                ...prev,
                                [r.path]: { ...pick, name: e.target.value },
                              }))
                            }
                          />
                        </td>
                        <td className="py-1 pr-2">{r.default_branch}</td>
                        <td className="py-1 pr-2 text-muted">
                          {r.last_commit_at
                            ? new Date(r.last_commit_at).toLocaleDateString()
                            : "—"}
                        </td>
                        <td className="py-1 pr-2">
                          <select
                            className="field !py-0.5 !w-auto"
                            value={pick.default_connector_id}
                            disabled={r.already_registered}
                            onChange={(e) =>
                              setPicks((prev) => ({
                                ...prev,
                                [r.path]: {
                                  ...pick,
                                  default_connector_id: e.target.value,
                                },
                              }))
                            }
                          >
                            <option value="">(none)</option>
                            {connectors.map((c) => (
                              <option key={c.connector_id} value={c.connector_id}>
                                {c.display_name}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="py-1 pr-2 text-muted">
                          {r.already_registered && (
                            <span className="tag">already registered</span>
                          )}
                          {!r.already_registered && r.has_uncommitted && (
                            <span className="tag">dirty</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <footer className="mt-3 flex flex-col-reverse items-stretch justify-end gap-2 sm:flex-row sm:items-center">
              <button className="btn w-full sm:w-auto" onClick={onClose}>
                cancel
              </button>
              <button
                className="btn btn-primary w-full sm:w-auto"
                onClick={submit}
                disabled={register.isPending || selectedCount === 0}
              >
                {register.isPending
                  ? "Registering…"
                  : `Register ${selectedCount}`}
              </button>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
