# 01 — Authentication & Security (Cerberus)

Source: `backend/daedalus/auth/`, `backend/daedalus/anomaly.py`,
`backend/daedalus/api/routes/auth.py`, `backend/daedalus/api/routes/webauthn.py`.

## 3-step login flow

| Step | Factor | Endpoint | Notes |
|------|--------|----------|-------|
| 1 | Password | `POST /api/v1/auth/password` | Argon2id verify, issues email OTP |
| 2 | Email OTP | `POST /api/v1/auth/email-otp` | 8-digit code or magic-link token |
| 3 | TOTP / recovery / WebAuthn | `POST /api/v1/auth/totp` | session created on success |

- `GET /api/v1/auth/status` — boot probe (`authenticated`, user info).
- `POST /api/v1/auth/logout` — session revocation.
- **Server-side stage gating** in Redis prevents factor-skipping; password→OTP window
  15 min, OTP→TOTP window 5 min (`auth.py:37-59`).

## Password authentication

- **Argon2id** hashing (64 MB, t=3, p=4) with an HMAC-SHA256 server-side pepper
  (`passwords.py:13-22`).
- **Password policy**: ≥14 chars, 4 character classes, common-password blocklist
  (`policy.py:11-34`).
- **Auto-rehash** of weak hashes on successful login (`auth.py:199-200`).

## Email OTP

- Cryptographic 8-digit code **and** URL-safe 32-byte magic-link token (`email_otp.py:24-29`).
- 15-minute TTL, single-use (`used_at`), constant-time comparison (`email_otp.py:60-91`).
- Captures issuing IP + cert fingerprint for audit (`email_otp.py:32-57`).
- Delivered via async SMTP (aiosmtplib), best-effort — delivery failure doesn't block
  flow (`smtp.py:13-35`). **Note:** real Gmail delivery in current deployment.

## TOTP & recovery codes

- RFC 6238, 32-byte base32 secret, issuer "Daedalus", ±1 window tolerance (`totp.py:52-63`).
- Secret **encrypted at rest** with Fernet (key from `TOTP_ENC_KEY` or derived from pepper);
  legacy plaintext secrets auto-migrate on next login (`totp.py:20-46`, `auth.py:302-303`).
- **10 recovery codes** per user, BLAKE2b-hashed with pepper, single-use (`totp.py:68-83`).

## WebAuthn / hardware keys

- Full register + authenticate ceremonies (`webauthn_svc.py:58-171`).
- Multiple keys per user with nicknames; **sign-count tracking** detects cloned keys;
  last-used timestamps (`webauthn_svc.py:162-199`).
- Supports ECDSA-SHA256, EdDSA, RSA-2048 (`webauthn_svc.py:80-84`).
- Challenges in Redis, 5-min TTL (`webauthn.py:28-29`).
- WebAuthn can **substitute for the TOTP step** (`webauthn.py:172-232`).
- Endpoints: `register/begin`, `register/finish`, `GET credentials`,
  `DELETE credentials/{pk}`, `authenticate/begin`, `authenticate/finish`.

## mTLS client certificates

- CLI mints RSA-4096 client certs (365-day) signed by internal CA → `.key`/`.crt`/`.p12`
  (0600) (`client_certs.py:68-196`).
- SHA-256 fingerprint matches Caddy format (`client_certs.py:178-185`).
- Caddy terminates mTLS and forwards `X-Client-Cert-Fingerprint`; API trusts the header
  (`dependencies.py:31-38`). **API must never be exposed directly.**
- **Per-user cert pinning** (`User.pinned_cert_fingerprint`); auto-pin on first login;
  mismatch is rejected and audited (`auth.py:201-207`).
- Sessions bound to exact cert fingerprint, verified per request (`sessions.py:44-64`).
- Toggle via `REQUIRE_CLIENT_CERT` (sentinel when off, for Tailscale-only deploys).

## Sessions

- Cookie-based signed sessions (itsdangerous TimestampSigner, salt `daedalus.sess`)
  (`sessions.py:18-31`).
- Bound to cert fingerprint + source IP (`sessions.py:34-51`).
- **30-min idle timeout** (re-auth required), **12-hour hard expiry**; revocation
  (`sessions.py:73-87`, `settings.py:31-32`).
- Cookies: HTTPOnly, Secure, SameSite=Strict (`auth.py:313-316`).

## Lockout & rate limiting

- Per-account lockout: 5 failures → 15-min lock (`settings.py:33-34`).
- Per-IP ban: 25 failures → 60-min ban, Redis-backed (`settings.py:35-36`, `auth.py:118-162`).
- Constant-time failure path to prevent user enumeration (`auth.py:184`).

## Audit log & anomaly detection

- Every mutation + auth event recorded: actor (user/IP/cert), target, structured JSON
  payload (`audit.py:11-33`); owner-only `GET /api/v1/audit` with filters.
- **Anomaly detection** (`anomaly.py`), scanned every ~120 s by Hermes bookkeeper, 4 rules:
  1. IP auth-failure burst (brute force) — default threshold 15.
  2. Cert-mismatch spike — default 5.
  3. Single-account failures across ≥4 distinct IPs (credential stuffing).
  4. Bulk `*.delete` by one actor (default 20).
- Severity levels, per-(rule, subject) Redis cooldown dedup, self-loop prevention
  (`anomaly.py:108-247`). All thresholds are `ANOMALY_*` env knobs (0 disables a rule).

## Configuration

`SESSION_SECRET`, `PASSWORD_PEPPER` (required), `TOTP_ENC_KEY` (optional),
`DAEDALUS_PUBLIC_URL` (WebAuthn RP-ID + magic links), `LOCKOUT_*`, `IP_BAN_*`,
`SESSION_IDLE_MINUTES`, `SESSION_HARD_HOURS`, `ANOMALY_*`, `SMTP_*` (`core/settings.py`).
