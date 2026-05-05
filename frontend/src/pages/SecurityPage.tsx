import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiJson } from "../api";
import { useApp } from "../store";
import {
  serializeAttestation,
  webauthnCreateOptions,
} from "./LoginPage";

interface Cred {
  id: string;
  nickname: string | null;
  transports: string | null;
  last_used_at: string | null;
  created_at: string;
}

export default function SecurityPage() {
  const flash = useApp((s) => s.flash);
  const qc = useQueryClient();
  const [nickname, setNickname] = useState("Hardware key");
  const creds = useQuery<Cred[]>({
    queryKey: ["webauthn-creds"],
    queryFn: () => api("/api/v1/auth/webauthn/credentials"),
  });

  const enroll = useMutation({
    mutationFn: async () => {
      const opts: any = await apiJson("/api/v1/auth/webauthn/register/begin", {});
      const cred = (await navigator.credentials.create({
        publicKey: webauthnCreateOptions(opts),
      })) as PublicKeyCredential;
      return apiJson("/api/v1/auth/webauthn/register/finish", {
        nickname,
        response: serializeAttestation(cred),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webauthn-creds"] });
      flash("Hardware key enrolled", "success");
    },
    onError: (err: any) => flash(err.message || "Enrollment failed", "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/auth/webauthn/credentials/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webauthn-creds"] }),
  });

  return (
    <section className="panel space-y-4">
      <h2 className="text-sm uppercase tracking-wide text-muted">Security</h2>

      <div>
        <h3 className="mb-2 text-xs uppercase tracking-wide text-muted">
          WebAuthn / hardware keys
        </h3>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="px-2 py-1">nickname</th>
              <th className="px-2 py-1">transports</th>
              <th className="px-2 py-1">last used</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {creds.data?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-2 py-2 text-muted">
                  No hardware keys yet — enroll one below to skip TOTP at login.
                </td>
              </tr>
            )}
            {creds.data?.map((c) => (
              <tr key={c.id} className="border-t border-border">
                <td className="px-2 py-1">{c.nickname || "(unnamed)"}</td>
                <td className="px-2 py-1 text-muted">{c.transports || "—"}</td>
                <td className="px-2 py-1 text-muted">
                  {c.last_used_at ? new Date(c.last_used_at).toLocaleString() : "—"}
                </td>
                <td className="px-2 py-1 text-right">
                  <button className="btn btn-danger" onClick={() => remove.mutate(c.id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 flex items-end gap-2">
          <div className="flex-1">
            <label className="label">Nickname</label>
            <input
              className="field"
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={() => enroll.mutate()}
            disabled={enroll.isPending}
          >
            {enroll.isPending ? "Awaiting key…" : "Enroll new key"}
          </button>
        </div>
      </div>
    </section>
  );
}
