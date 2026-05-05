import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, apiJson } from "../api";
import { useApp } from "../store";

type Step = "password" | "otp" | "totp";

export default function LoginPage() {
  const navigate = useNavigate();
  const { setAuthed, setEmail, email, flash } = useApp();
  const [step, setStep] = useState<Step>("password");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);

  async function submitPassword(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await apiJson("/api/v1/auth/password", { email, password });
      setStep("otp");
      flash("Password accepted. Check your email for the OTP.", "success");
    } catch (err: any) {
      flash(err.message || "Password step failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitOtp(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await apiJson("/api/v1/auth/email-otp", { email, code: otp });
      setStep("totp");
      flash("Email OTP accepted. Finish with TOTP / hardware key.", "success");
    } catch (err: any) {
      flash(err.message || "OTP step failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitTotp(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await apiJson("/api/v1/auth/totp", { email, code: totp });
      setAuthed(true);
      navigate("/", { replace: true });
    } catch (err: any) {
      flash(err.message || "TOTP step failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function authWithWebAuthn() {
    if (!email) {
      flash("Enter your email first", "error");
      return;
    }
    setBusy(true);
    try {
      const opts: any = await apiJson("/api/v1/auth/webauthn/authenticate/begin", { email });
      const cred = await navigator.credentials.get({
        publicKey: webauthnGetOptions(opts),
      });
      const payload = serializeAssertion(cred as PublicKeyCredential);
      await apiJson("/api/v1/auth/webauthn/authenticate/finish", {
        email,
        response: payload,
      });
      setAuthed(true);
      navigate("/", { replace: true });
    } catch (err: any) {
      flash(err.message || "WebAuthn failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="panel w-[420px]">
        <h1 className="mb-1 text-xl font-semibold text-accent">DAEDALUS</h1>
        <p className="mb-4 text-xs text-muted">
          mTLS &amp; 3-factor login. Your client cert was already accepted at TLS handshake.
        </p>
        {step === "password" && (
          <form onSubmit={submitPassword} className="space-y-3">
            <div>
              <label className="label">Email</label>
              <input
                className="field"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                className="field"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <button className="btn btn-primary w-full" disabled={busy}>
              {busy ? "Verifying…" : "Continue"}
            </button>
            <div className="border-t border-border pt-3">
              <button
                type="button"
                onClick={authWithWebAuthn}
                disabled={busy}
                className="btn w-full"
              >
                Use a hardware key (skip 3-step)
              </button>
            </div>
          </form>
        )}
        {step === "otp" && (
          <form onSubmit={submitOtp} className="space-y-3">
            <div>
              <label className="label">Email OTP</label>
              <input
                className="field"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                placeholder="8 digits"
                autoFocus
                required
              />
            </div>
            <button className="btn btn-primary w-full" disabled={busy}>
              {busy ? "Verifying…" : "Verify"}
            </button>
          </form>
        )}
        {step === "totp" && (
          <form onSubmit={submitTotp} className="space-y-3">
            <div>
              <label className="label">TOTP / Recovery code</label>
              <input
                className="field"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6 digits"
                autoFocus
                required
              />
            </div>
            <button className="btn btn-primary w-full" disabled={busy}>
              {busy ? "Logging in…" : "Log in"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

// --- WebAuthn helpers ---

function b64urlToBuf(b64: string): ArrayBuffer {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const str = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let str = "";
  for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function webauthnGetOptions(opts: any): PublicKeyCredentialRequestOptions {
  return {
    ...opts,
    challenge: b64urlToBuf(opts.challenge),
    allowCredentials: (opts.allowCredentials || []).map((c: any) => ({
      ...c,
      id: b64urlToBuf(c.id),
    })),
  };
}

export function webauthnCreateOptions(opts: any): PublicKeyCredentialCreationOptions {
  return {
    ...opts,
    challenge: b64urlToBuf(opts.challenge),
    user: { ...opts.user, id: b64urlToBuf(opts.user.id) },
    excludeCredentials: (opts.excludeCredentials || []).map((c: any) => ({
      ...c,
      id: b64urlToBuf(c.id),
    })),
  };
}

export function serializeAssertion(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64url(r.clientDataJSON),
      authenticatorData: bufToB64url(r.authenticatorData),
      signature: bufToB64url(r.signature),
      userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
    },
  };
}

export function serializeAttestation(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64url(r.clientDataJSON),
      attestationObject: bufToB64url(r.attestationObject),
      transports: (r as any).getTransports?.() ?? [],
    },
  };
}
