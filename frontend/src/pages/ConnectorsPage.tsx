import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Connector, api, apiJson } from "../api";
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

  return (
    <section className="panel">
      <h2 className="mb-3 text-sm uppercase tracking-wide text-muted">Connectors</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-muted">
            <th className="px-2 py-1">id</th>
            <th className="px-2 py-1">display name</th>
            <th className="px-2 py-1">profile</th>
            <th className="px-2 py-1">status</th>
            <th className="px-2 py-1"></th>
          </tr>
        </thead>
        <tbody>
          {connectors.data?.map((c) => (
            <tr key={c.connector_id} className="border-t border-border">
              <td className="px-2 py-1 font-mono text-xs">{c.connector_id}</td>
              <td className="px-2 py-1">{c.display_name}</td>
              <td className="px-2 py-1">
                <span className="tag">{c.spec.permission_profile || "?"}</span>
              </td>
              <td className="px-2 py-1">
                <span
                  className={`status-pill ${
                    c.enabled ? "status-done" : "status-cancelled"
                  }`}
                >
                  {c.enabled ? "enabled" : "disabled"}
                </span>
              </td>
              <td className="px-2 py-1 text-right">
                <button
                  className="btn"
                  onClick={() =>
                    toggle.mutate({ id: c.connector_id, enable: !c.enabled })
                  }
                >
                  {c.enabled ? "Disable" : "Enable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
