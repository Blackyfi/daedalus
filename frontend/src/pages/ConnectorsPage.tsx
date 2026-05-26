import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ANTHROPIC_MODELS, Connector, api, apiJson } from "../api";
import { useApp } from "../store";

export default function ConnectorsPage() {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const connectors = useQuery<Connector[]>({
    queryKey: ["connectors-all"],
    queryFn: () => api("/api/v1/connectors?include_disabled=true"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enable }: { id: string; enable: boolean }) =>
      apiJson(`/api/v1/connectors/${id}/${enable ? "enable" : "disable"}`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors-all"] });
      qc.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err: any) => flash(err.message || "Toggle failed", "error"),
  });

  // Re-import the on-disk connector pack into the DB (owner-only). Lets you
  // drop edited specs into connectors/ and pick them up without a restart.
  const reload = useMutation({
    mutationFn: () =>
      apiJson<{ imported: number; added: number; updated: number }>(
        "/api/v1/connectors/reload",
        {},
      ),
    onSuccess: (r) => {
      flash(
        `Reloaded connector pack: ${r.imported} spec${r.imported === 1 ? "" : "s"} (${r.added} added, ${r.updated} updated)`,
        "success",
      );
      qc.invalidateQueries({ queryKey: ["connectors-all"] });
      qc.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err: any) => flash(err.message || "Reload failed", "error"),
  });

  return (
    <section className="panel">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm uppercase tracking-wide text-muted">Connectors</h2>
        <button
          className="btn"
          onClick={() => reload.mutate()}
          disabled={reload.isPending}
          title="Re-import the on-disk connector pack into the database"
        >
          {reload.isPending ? "Reloading…" : "⟳ Reload pack"}
        </button>
      </div>
      <div className="-mx-3 overflow-x-auto sm:-mx-4 lg:mx-0">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="px-2 py-1">id</th>
              <th className="px-2 py-1">display name</th>
              <th className="px-2 py-1">profile</th>
              <th className="px-2 py-1">status</th>
              <th className="px-2 py-1">override</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {connectors.data?.map((c) => (
              <ConnectorRow
                key={c.connector_id}
                connector={c}
                onToggleEnable={() =>
                  toggle.mutate({ id: c.connector_id, enable: !c.enabled })
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

interface OverrideForm {
  force_project_overrides: boolean;
  override_planning_model: string;
  override_task_model: string;
  override_verifier_model: string;
  override_wall_clock_minutes: string;
  override_argus_enabled: "" | "true" | "false";
  override_max_fix_loops: string;
}

function fromConnector(c: Connector): OverrideForm {
  return {
    force_project_overrides: c.force_project_overrides,
    override_planning_model: c.override_planning_model ?? "",
    override_task_model: c.override_task_model ?? "",
    override_verifier_model: c.override_verifier_model ?? "",
    override_wall_clock_minutes:
      c.override_wall_clock_minutes == null ? "" : String(c.override_wall_clock_minutes),
    override_argus_enabled:
      c.override_argus_enabled == null ? "" : c.override_argus_enabled ? "true" : "false",
    override_max_fix_loops:
      c.override_max_fix_loops == null ? "" : String(c.override_max_fix_loops),
  };
}

function ConnectorRow({
  connector,
  onToggleEnable,
}: {
  connector: Connector;
  onToggleEnable: () => void;
}) {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<OverrideForm>(() => fromConnector(connector));

  // Re-sync from server whenever the connector row changes (e.g. after a
  // save). Only resets when collapsed so we don't yank values mid-edit.
  useEffect(() => {
    if (!open) setForm(fromConnector(connector));
  }, [connector, open]);

  const save = useMutation({
    mutationFn: () =>
      apiJson<Connector>(
        `/api/v1/connectors/${connector.connector_id}/overrides`,
        {
          force_project_overrides: form.force_project_overrides,
          override_planning_model: form.override_planning_model || null,
          override_task_model: form.override_task_model || null,
          override_verifier_model: form.override_verifier_model || null,
          override_wall_clock_minutes:
            form.override_wall_clock_minutes === ""
              ? null
              : Number(form.override_wall_clock_minutes),
          override_argus_enabled:
            form.override_argus_enabled === ""
              ? null
              : form.override_argus_enabled === "true",
          override_max_fix_loops:
            form.override_max_fix_loops === ""
              ? null
              : Number(form.override_max_fix_loops),
        },
        { method: "PATCH" },
      ),
    onSuccess: () => {
      flash("Connector overrides saved", "success");
      qc.invalidateQueries({ queryKey: ["connectors-all"] });
      qc.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err: any) => flash(err.message || "Save failed", "error"),
  });

  const baseline = fromConnector(connector);
  const dirty =
    form.force_project_overrides !== baseline.force_project_overrides ||
    form.override_planning_model !== baseline.override_planning_model ||
    form.override_task_model !== baseline.override_task_model ||
    form.override_verifier_model !== baseline.override_verifier_model ||
    form.override_wall_clock_minutes !== baseline.override_wall_clock_minutes ||
    form.override_argus_enabled !== baseline.override_argus_enabled ||
    form.override_max_fix_loops !== baseline.override_max_fix_loops;

  const overrideStatus = connector.force_project_overrides
    ? "forcing"
    : "off";

  return (
    <>
      <tr className="border-t border-border">
        <td className="max-w-[180px] truncate px-2 py-1 font-mono text-xs">
          {connector.connector_id}
        </td>
        <td className="px-2 py-1">{connector.display_name}</td>
        <td className="px-2 py-1">
          <span className="tag">{connector.spec.permission_profile || "?"}</span>
        </td>
        <td className="px-2 py-1">
          <span
            className={`status-pill ${
              connector.enabled ? "status-done" : "status-cancelled"
            }`}
          >
            {connector.enabled ? "enabled" : "disabled"}
          </span>
        </td>
        <td className="px-2 py-1">
          <span
            className={`status-pill ${
              connector.force_project_overrides ? "status-done" : "status-cancelled"
            }`}
          >
            {overrideStatus}
          </span>
        </td>
        <td className="px-2 py-1 text-right">
          <button
            className="btn"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "▾ Settings" : "▸ Settings"}
          </button>{" "}
          <button className="btn" onClick={onToggleEnable}>
            {connector.enabled ? "Disable" : "Enable"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-t border-border bg-bg-soft">
          <td colSpan={6} className="px-3 py-3">
            <div className="space-y-3 text-sm">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 cursor-pointer accent-accent"
                  checked={form.force_project_overrides}
                  onChange={(e) =>
                    setForm((s) => ({
                      ...s,
                      force_project_overrides: e.target.checked,
                    }))
                  }
                />
                <span>
                  <strong>Force overrides on all projects using this connector.</strong>{" "}
                  When on, the values below replace each project's own settings until
                  you switch it back off.
                </span>
              </label>
              <p className="text-[11px] text-muted">
                Each field below is independent — leave one as "(use project's value)"
                and that field stays per-project even when the toggle is on. Useful
                when you've burned through your Opus quota and want every project to
                fall back to Sonnet for a few hours.
              </p>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <ModelField
                  label="Planning model"
                  value={form.override_planning_model}
                  onChange={(v) =>
                    setForm((s) => ({ ...s, override_planning_model: v }))
                  }
                />
                <ModelField
                  label="Task model"
                  value={form.override_task_model}
                  onChange={(v) =>
                    setForm((s) => ({ ...s, override_task_model: v }))
                  }
                />
                <ModelField
                  label="Verifier model"
                  value={form.override_verifier_model}
                  onChange={(v) =>
                    setForm((s) => ({ ...s, override_verifier_model: v }))
                  }
                />

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted">
                    Wall-clock cap (minutes)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    placeholder="(use project's value)"
                    className="field"
                    value={form.override_wall_clock_minutes}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        override_wall_clock_minutes: e.target.value,
                      }))
                    }
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted">Argus verification</label>
                  <select
                    className="field"
                    value={form.override_argus_enabled}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        override_argus_enabled: e.target
                          .value as OverrideForm["override_argus_enabled"],
                      }))
                    }
                  >
                    <option value="">(use project's value)</option>
                    <option value="true">force enabled</option>
                    <option value="false">force disabled</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs text-muted">Max fix loops</label>
                  <input
                    type="number"
                    min={0}
                    max={20}
                    placeholder="(use project's value)"
                    className="field"
                    value={form.override_max_fix_loops}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        override_max_fix_loops: e.target.value,
                      }))
                    }
                  />
                </div>
              </div>

              <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between">
                <button
                  className="btn w-full sm:w-auto"
                  onClick={() => setForm(fromConnector(connector))}
                  disabled={!dirty || save.isPending}
                >
                  Reset
                </button>
                {!dirty && !save.isPending && (
                  <span className="hidden text-[11px] text-muted sm:inline">
                    no changes
                  </span>
                )}
                <button
                  className="btn btn-primary w-full sm:w-auto"
                  onClick={() => save.mutate()}
                  disabled={!dirty || save.isPending}
                >
                  {save.isPending ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ModelField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted">{label}</label>
      <select
        className="field"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">(use project's value)</option>
        {ANTHROPIC_MODELS.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} — {m.tier}
          </option>
        ))}
      </select>
    </div>
  );
}
