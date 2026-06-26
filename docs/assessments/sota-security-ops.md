# Daedalus — Security & Self-Hosted-Ops Assessment vs. 2025–2026 State of the Art

**Date:** 2026-06-26
**Scope:** Sandboxing/isolation, prompt-injection defenses, secrets handling, WebAuthn/mTLS/zero-trust, audit/anomaly/SIEM-lite, supply-chain (connector signing), and self-hosted operability (backup/restore, secret rotation, migration safety, observability, DR).
**Method:** Web research against primary sources (NIST, OWASP, gVisor/Firecracker/Landlock docs, FIDO Alliance, Sigstore/SLSA, PostgreSQL/MinIO docs, vendor engineering blogs 2024–2026). Citations inline and collected per section.

> **Through-line.** Daedalus is genuinely *above* the average self-hosted tool — it has real egress firewalling, cgroups limits, a 3-step phishing-resistant-capable login, per-user cert pinning, a queryable audit log with behavioral anomaly rules, and a modern observability stack. But it is built on a **pre-2025 "stack more controls" philosophy**, while the modern bar has shifted to **strong isolation boundaries, phishing resistance, tamper-evidence, verifiable provenance, and tested recovery**. Three design choices are now affirmatively the *wrong* pattern: (1) shared-kernel runners with long-lived credentials bind-mounted in; (2) email OTP + a plaintext forwarded cert-fingerprint header as identity; (3) hot-reloading unsigned connector packs (a latent RCE). This document maps each area to STRONG / LAGS / concrete upgrade.

---

## 1. Sandboxing & isolation for agents with shell access

### Modern bar
Industry has converged on an isolation tiering for running untrusted / agent-generated code:

- **Tier 0 — plain containers (shared host kernel).** Namespaces + cgroups + maybe a default seccomp profile. 2025–2026 consensus: *"your container is not a sandbox"* — one host-kernel LPE in the ~350–450 syscall surface = full host compromise. **This is where Daedalus is today.**
- **Tier 1 — hardened OS primitives (what the agent CLIs themselves ship).** Anthropic **Claude Code** uses Linux **bubblewrap** + **seccomp-bpf** (blocks Unix-domain sockets) / macOS Seatbelt, FS confined to CWD, and **no direct egress — all traffic forced through a host-side domain-allowlisting proxy over a Unix socket**. OpenAI **Codex CLI** uses **bubblewrap + Landlock LSM + seccomp**, network off by default, writes confined to the workspace.
- **Tier 2 — user-space kernel: gVisor (`runsc`).** A Go re-implementation of the Linux ABI (the Sentry) intercepts syscalls; only a small vetted set reaches the host, guarded by a second seccomp layer. Host-kernel CVEs are unreachable via the normal path. ~10–30% I/O overhead. Used by **Google GKE Sandbox, Modal, Daytona**.
- **Tier 3 — hardware-virtualized microVMs: Firecracker / Kata.** Each run gets its own guest kernel behind KVM + the Firecracker jailer (chroot/ns/cgroup + thread-specific seccomp). The recognized **gold standard for untrusted/multi-tenant code**. Used by **AWS Lambda/Fargate, E2B, Fly.io Sprites, Northflank (Kata)**.

The emerging Kubernetes-native pattern is `kubernetes-sigs/agent-sandbox`, which decouples agent lifecycle from the isolation backend (swap gVisor ↔ Kata).

### Daedalus: STRONG
- **Default-deny egress via per-connector allowlists ("agentnet").** Architecturally correct and *ahead* of many setups — matches the instinct behind Claude Code's proxy and Cloudflare's egress proxy. Host-level iptables is a legitimate, tamper-resistant enforcement point and directly blunts data-exfil and C2/crypto-mining callouts.
- **cgroups v2 per-run limits.** Necessary and correct — even gVisor and Firecracker rely on host cgroups for resource-exhaustion defense (fork/CPU/memory/storage bombs, crypto-miner CPU pinning). Daedalus already has the layer the strong sandboxes assume beneath them.

### Daedalus: LAGS
1. **Shared host kernel, full syscall surface** (the big one). A single kernel LPE turns an agent run into host compromise — Tier 0, *below* the agent CLIs' own Tier-1 defaults.
2. **No seccomp-bpf profile.** Nothing reduces syscall surface — the cheapest high-value upgrade; table stakes for Firecracker/gVisor/Codex/Claude Code.
3. **No mandatory FS confinement** (Landlock/bwrap/AppArmor). `rm -rf` blast radius and credential theft depend entirely on container config, vs. kernel-enforced workspace confinement in the agent CLIs.
4. **Egress enforced only at L3/L4 iptables**, not also application-layer. Bypassable by DNS exfil, tunneling over an *allowed* domain/IP (e.g. a permitted GitHub/S3 endpoint as a dead-drop), or SNI/domain fronting. No HTTP/SOCKS5 filtering proxy with domain+path inspection.
5. **No KVM or user-space-kernel boundary** — no defense against the kernel-CVE container escape that E2B/Fly/Lambda/GKE explicitly chose microVMs/gVisor to stop.

### Concrete upgrade (staged)
1. **Now (days):** add **seccomp-bpf + Landlock + drop-all-caps + user namespaces (rootless)** to every runner — adopt **bubblewrap** per run or wrap with the open-source `anthropic-experimental/sandbox-runtime`. Gets Daedalus from Tier 0 → Tier 1 with no architecture change.
2. **Short term:** front the egress firewall with an **application-layer filtering proxy** (HTTP/SOCKS5, domain+SNI+path allowlist, controlled DNS) — keep iptables as the L3 default-deny backstop. Block raw Unix sockets via seccomp so the agent can't sidestep the proxy.
3. **Primary recommendation:** wrap runners in the **gVisor `runsc` runtime** (drop-in `--runtime=runsc` / k8s `RuntimeClass`) — the decisive escape-resistance jump to Tier 2. Caveat: blocks GPU passthrough.
4. **End-state for high-risk/multi-tenant runs:** **Kata Containers** (keeps OCI/k8s workflow) or **Firecracker** microVMs — own guest kernel per run, with snapshot/restore for warm starts. Requires KVM/nested-virt.

### Sources
- Anthropic — *Making Claude Code more secure with sandboxing* — https://www.anthropic.com/engineering/claude-code-sandboxing (bwrap/Seatbelt, CWD-confined FS, egress proxy over Unix socket)
- Claude Code docs — *Sandboxed Bash tool* — https://code.claude.com/docs/en/sandboxing (seccomp blocks Unix sockets; proxy config)
- `anthropic-experimental/sandbox-runtime` — https://github.com/anthropic-experimental/sandbox-runtime (reusable no-container FS/net restriction Daedalus could adopt)
- OpenAI — *Codex agent approvals & security* — https://developers.openai.com/codex/agent-approvals-security (network-off default, workspace-only writes)
- Codex sandboxing impl — https://deepwiki.com/openai/codex/5.6-sandboxing-implementation (Linux = bwrap + Landlock + seccomp)
- gVisor security model — https://gvisor.dev/docs/architecture_guide/security/ ; seccomp layer — https://gvisor.dev/blog/2024/02/01/seccomp/
- Firecracker threat model — https://deepwiki.com/firecracker-microvm/firecracker/6.2-security-architecture-and-threat-model ; design — https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md
- Landlock — https://docs.kernel.org/userspace-api/landlock.html
- gVisor/Kata RuntimeClass how-to — https://www.systemshardening.com/articles/kubernetes/runtimeclass-gvisor-kata/
- Northflank — *How to sandbox AI agents in 2026* — https://northflank.com/blog/how-to-sandbox-ai-agents ; "Your container is not a sandbox" — https://emirb.github.io/blog/microvm-2026/
- E2B/Daytona/Firecracker landscape — https://www.spheron.network/blog/ai-agent-code-execution-sandbox-e2b-daytona-firecracker/ ; Modal — https://modal.com/resources/best-code-execution-sandboxes-ai-agents ; Fly Sprites — https://fly.io/learn/agent-sandbox/ ; Cloudflare Sandboxes GA — https://www.infoq.com/news/2026/04/cloudflare-sandboxes-ga/
- NIST SP 800-190 (container security) — https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-190.pdf
- Anthropic Claude Code sandbox-bypass patch (Tier-1 sandboxes still get bypassed → layer up) — https://www.securityweek.com/anthropic-silently-patches-claude-code-sandbox-bypass/

---

## 2. Prompt-injection / agent-hijack defenses

### Modern bar
Hard consensus: **prompt injection is unsolved at the model level and cannot be patched away** — instructions and data share one channel (OWASP LLM01:2025; Willison). The bar is *architectural containment*, not detection:

- **The "lethal trifecta" (Willison, Jun 2025):** danger exists only when an agent simultaneously has (1) access to private data, (2) exposure to untrusted content, and (3) an external-communication/exfiltration path. Break any one leg and the exfiltration class collapses.
- **Meta's "Agents Rule of Two" (Oct 2025):** an unsupervised agent may hold at most **two** of {untrusted input, sensitive systems/secrets, ability to change state / communicate externally}. If all three are needed → **human-in-the-loop**.
- **Privilege separation / CaMeL (Google DeepMind, 2025):** a *privileged* LLM plans from the trusted query and emits a program; a *quarantined* LLM only parses untrusted data and can never influence control flow; capabilities/taint enforce data-use policy. Willison: "first credible mitigation."
- **Deterministic guardrails outside the model:** egress allowlists, tool allowlists, taint/quarantine of untrusted content, human approval for consequential actions. A "95%/99% block rate" classifier is a *failure rate* in security terms.
- **Treat agent config files as executable code.** 2025–2026 incident wave showed `.cursor/rules`, `copilot-instructions`, `CLAUDE.md`, READMEs and issues are injection vectors — including via **invisible Unicode** (zero-width, bidi, Unicode Tag block).

Real incidents: "Rules File Backdoor" (Pillar), GitLab Duo remote injection → source-code theft, CVE-2025-53773 (wormable Copilot RCE), CVE-2025-65099 (Claude Code), CVE-2025-62222 (Copilot). Survey work found *every* major AI IDE tested had ≥1 exploitable injection→exec/exfil chain.

### Daedalus: STRONG
- Local-first/self-hosted reduces multi-tenant blast radius and third-party data egress.
- Per-run runners give an enforcement substrate for taint boundaries and approval gates (the hard part of CaMeL/Rule-of-Two is *having* an enforcement point).

### Daedalus: LAGS
- **Holds all three trifecta legs at once, unsupervised:** untrusted repo/issue/PR/web content **+** private data & real credentials (mounted `~/.claude`, `~/.ssh`) **+** shell and web egress. Violates both Rule of Two and the trifecta directly.
- **No provenance/quarantine boundary:** untrusted repo text, web pages, and config files (`CLAUDE.md`, rule files) flow into the same privileged context that calls shell tools — the CaMeL anti-pattern.
- **Likely no invisible-Unicode sanitization** of ingested content or config.
- **Web browsing + shell = open exfil channel:** any injected instruction can `curl` private data out regardless of data scoping.

### Concrete upgrade
1. **Cut a trifecta leg by default — kill unrestricted egress** via the §1 filtering proxy (Claude Code pattern). Single highest-leverage injection mitigation; neutralizes most exfil even when injection succeeds.
2. **Quarantine untrusted content (CaMeL-style):** route cloned repo bodies, issue/PR text, and fetched web pages through a separate parse/summarize step whose output is *data*, not instructions, and which cannot trigger tool calls.
3. **Enforce Rule of Two operationally:** when a run needs untrusted input **and** credentialed/state-changing actions **and** network, require **human approval** for consequential tool calls (git push, network writes, secret reads).
4. **Sanitize/strip invisible Unicode** from all ingested repo/web/config content; treat `CLAUDE.md`/rule files as untrusted executable input subject to review (Pillar's free rule scanner for triage).
5. **Both isolations, always** (Anthropic): effective sandboxing requires *both* filesystem and network isolation.

### Sources
- Willison — *The lethal trifecta* — https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- Meta — *Agents Rule of Two* — https://ai.meta.com/blog/practical-ai-agent-security/
- Google DeepMind — CaMeL (*Defeating Prompt Injections by Design*) — https://arxiv.org/abs/2503.18813 ; Willison analysis — https://simonwillison.net/2025/Apr/11/camel/
- OWASP LLM Top 10 (2025) — https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf
- Anthropic Claude Code sandboxing (both isolations; keeps signing creds outside sandbox via proxy) — https://www.anthropic.com/engineering/claude-code-sandboxing
- Pillar — "Rules File Backdoor" (invisible-Unicode injection) — https://www.pillar.security/blog/new-vulnerability-in-github-copilot-and-cursor-how-hackers-can-weaponize-code-agents
- Legit Security — GitLab Duo remote injection → source-code theft — https://www.legitsecurity.com/blog/remote-prompt-injection-in-gitlab-duo
- CVE-2025-53773 wormable Copilot RCE — https://www.persistent-security.net/post/part-iii-vscode-copilot-wormable-command-execution-via-prompt-injection
- *Your AI, My Shell* (injection→shell across AI IDEs) — https://arxiv.org/html/2509.22040v2
- CSA — README instruction-injection note (2026) — https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/03/CSA_research_note_readme_instruction_injection_ai_coding_agents_20260317-csa-styled.pdf
- Claude Code network-allowlist bypass (proxies are defense-in-depth, not infallible) — https://oddguan.com/blog/second-time-same-sandbox-anthropic-claude-code-network-allowlist-bypass-data-exfiltration/

---

## 3. Secrets handling for agents

### Modern bar
Direction is **secret-less / short-lived / just-in-time**, keeping long-lived credentials **OUT** of the execution environment:

- **Short-lived dynamic secrets (Vault):** creds generated on demand with minute/hour TTL, auto-revoked at lease expiry; 2025 guidance explicitly targets *agentic runtimes* (OAuth2/token-exchange with user attribution, RBAC, audit).
- **Workload identity / secret-less auth (SPIFFE/SPIRE, CNCF):** machine-attested short-lived SVIDs (~1h, auto-renewed) — "no long-lived secrets to leak." SPIRE can exchange a JWT-SVID for short-lived cloud tokens.
- **Credential broker pattern:** the agent never holds the secret; a broker performs the privileged op (sign, push, deploy) on its behalf, with policy + audit + optional confirmation. **This is exactly Anthropic's pattern for Claude Code on the web: git credentials and signing keys are kept *outside* the sandbox, handled through a secure proxy.**
- **SSH specifically:** prefer **ephemeral/short-lived SSH certificates** (smallstep, Teleport) over long-lived keys; if forwarding an agent, do it **per-session with `ssh-add -c` confirm-on-use + `-t` timeout + `-x` lock**, never a blanket mount.

### Daedalus: STRONG
- Self-hosted/local = full control of the trust boundary; can insert a broker/proxy without a vendor.
- Using real OAuth Claude creds (vs. scattered static API keys) is marginally better *if* tokens are scoped and refreshable.

### Daedalus: LAGS — the most serious finding in this report
**Mounting `~/.claude` and `~/.ssh` directly into a runner that executes untrusted-influenced code is the single highest-risk choice in the system** — the exact scenario every source says to avoid:
- `~/.ssh` private keys are **long-lived, broadly-scoped, reusable**. Inside the runner they are readable by any injected `cat ~/.ssh/id_* | curl` chain. With web/shell egress present, that's one-step exfil of keys that likely grant push access to *all* repos and possibly server SSH access.
- `~/.claude` creds let an attacker run up cost, access the account, and pivot.
- This converts "the agent wrote bad code" into **"the attacker now has your durable identity"** — keys don't expire when the run ends.
- It satisfies trifecta leg #1 (private data = real creds) and amplifies leg #3 (exfil): injection → full credential compromise.

### Concrete upgrade (priority order)
1. **Stop bind-mounting `~/.ssh`.** Replace with **ephemeral, repo-scoped, short-lived SSH certificates / deploy keys** minted per-run (smallstep/Teleport CA, expiring at run end) — *or* a **signing/credential broker** where the key never enters the runner (Anthropic's pattern) — *or*, at minimum, per-session agent forwarding with `ssh-add -c` confirm-on-use.
2. **Stop bind-mounting `~/.claude`.** Inject a **short-lived scoped token** at run start via a broker; refresh as needed; never place the durable credential file inside an untrusted-code runner. Extend the existing per-project cost cap to per-run credential scoping.
3. **Put all secrets behind a broker + egress proxy** so even successful injection yields only short-TTL, narrowly-scoped, audited capabilities. Vault dynamic secrets or SPIFFE/SPIRE are the productized versions; a small local broker is the MVP.
4. **Audit-log every credential issuance/use** with run attribution so misuse is detectable, bounded, and revocable.

### Sources
- HashiCorp — short-lived credentials — https://www.hashicorp.com/en/blog/why-we-need-short-lived-credentials-and-how-to-adopt-them ; dynamic secrets — https://developer.hashicorp.com/vault/tutorials/db-credentials/database-secrets ; agentic-runtime security — https://www.hashicorp.com/en/products/vault/use-cases/agentic-runtime-security ; AI-agent identity pattern — https://developer.hashicorp.com/validated-patterns/vault/ai-agent-identity-with-hashicorp-vault
- SPIFFE/SPIRE concepts — https://spiffe.io/docs/latest/spire-about/spire-concepts/ ; OpenAI SPIFFE workload-identity federation — https://developers.openai.com/api/docs/guides/workload-identity-federation/spiffe
- Anthropic Claude Code sandboxing (signing keys kept outside sandbox via proxy) — https://www.anthropic.com/engineering/claude-code-sandboxing
- smallstep — ssh-agent / confirm-on-use — https://smallstep.com/blog/ssh-agent-explained/ ; Teleport — safe SSH agent forwarding — https://goteleport.com/blog/how-to-use-ssh-agent-safely/ ; agent-forwarding hijack risk — https://vincent.bernat.ch/en/blog/2020-safer-ssh-agent-forwarding

---

## 4. WebAuthn/passkey + mTLS / zero-trust

### Modern bar
Codified in **NIST SP 800-63-4 (final, July 2025):** the goal is **phishing resistance, not factor count**. Authenticators requiring **manual entry of an output SHALL NOT be considered phishing-resistant** — disqualifying passwords, TOTP, and **email/SMS OTP** alike. A FIDO2/WebAuthn **passkey with User Verification is already multi-factor** and beats "password + OTP." **AAL3** (right tier for admin tooling) requires a **non-exportable hardware-bound private key** (device-bound keys, not cloud-synced passkeys — synced = AAL2). Transport identity should be **short-lived, auto-rotated certs** (SPIFFE SVIDs / step-ca) per SP 800-207, fronted by an identity-aware proxy that passes **verifiable** identity to the backend.

### Daedalus: STRONG
- Two real phishing-resistant primitives are *available*: WebAuthn hardware keys and channel-bound mTLS client certs.
- **Argon2id** is the correct password hash. **Per-user cert pinning** defeats the classic "any cert from our CA" mTLS failure.
- Sessions **bound to cert fingerprint + IP** with idle/hard expiry largely defeat cookie-theft replay.
- The AAL3-capable building block (hardware keys) exists.

### Daedalus: LAGS
1. **Email OTP is now NIST-prohibited** for out-of-band auth ("Email SHALL NOT be used for out-of-band authentication") — phishable, friction without phishing resistance. This is the "MFA theater" element.
2. The stack optimizes **factor count over phishing resistance** — password + email OTP + TOTP are all manually entered and all defeated by real-time AitM proxies (Evilginx). Combined they're weaker than one UV passkey.
3. **`X-Client-Cert-Fingerprint` is spoofable** — the entire identity model rests on "the app is only ever reachable through Caddy." Any direct path (exposed port, SSRF, sidecar, header smuggling via `_`/`-` conflation) lets an attacker set the header and impersonate any pinned user.
4. **Long-lived hand-managed RSA-4096 certs** are the opposite of the zero-trust short-lived/auto-rotated model; a leaked key is valid until manually revoked. ECDSA P-256/Ed25519 is the modern default.
5. TOTP recovery codes are a static, phishable shared-secret fallback.

### Concrete upgrade
- Make **passkeys PRIMARY** with `userVerification:"required"`; require **device-bound** keys + attestation for admin/AAL3 roles; allow synced passkeys only at AAL2. Then drop the password+OTP+TOTP chain for passkey users.
- **Retire email OTP** as a factor; harden recovery so no email/SMS/password-only path bypasses the passkey.
- **Stop trusting a plaintext header:** network-enforce that the app accepts connections *only* from Caddy (mTLS on the proxy→app hop, Unix socket, or firewall/NetworkPolicy); strip inbound `X-Client-Cert-*` at the proxy root; pass identity as a **signed short-lived token (JWT/HMAC)** the app independently verifies — or terminate mTLS at the app.
- Move to **short-lived auto-rotated ECDSA/Ed25519 certs** (step-ca / SPIFFE-SPIRE).
- Consider an audited **identity-aware proxy** (Pomerium or Teleport, both self-hostable) to replace the bespoke Caddy-header design.

### Sources
- NIST SP 800-63B-4 (authenticators; phishing resistance; bans email for OOB; AAL3 non-exportable keys) — https://pages.nist.gov/800-63-4/sp800-63b/authenticators/ ; SP 800-63-4 final — https://pages.nist.gov/800-63-4/ ; Supplement 1 (syncable passkeys @ AAL2) — https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-63Bsup1.pdf
- FIDO Alliance — passkeys (UV = MFA; beats password+OTP) — https://fidoalliance.org/passkeys/ ; journey-to-prevent-phishing (remove all phishable login/recovery paths) — https://fidoalliance.org/wp-content/uploads/2025/03/Passkeys-The-Journey-to-Prevent-Phishing-Pt1.pdf
- CISA — implementing phishing-resistant MFA — https://www.cisa.gov/sites/default/files/publications/fact-sheet-implementing-phishing-resistant-mfa-508c.pdf
- NIST SP 800-207 Zero Trust — https://csrc.nist.gov/pubs/sp/800/207/final
- Header smuggling through reverse proxies — https://github.security.telekom.com/2020/05/smuggling-http-headers-through-reverse-proxies.html ; mTLS done wrong — https://github.blog/security/vulnerability-research/mtls-when-certificate-authentication-is-done-wrong/ ; concrete X.509-header spoof behind proxy — https://psytester.github.io/Keycloak_behind_reverse_proxy_spoof_X509_login_flow/
- SPIFFE/SPIRE short-lived SVIDs — https://www.redhat.com/en/topics/security/spiffe-and-spire ; Pomerium — https://www.pomerium.com/ ; Teleport BeyondCorp — https://goteleport.com/blog/how-teleport-extends-beyondcorp-and-federal-zero-trust-strategy/

---

## 5. Audit logging & anomaly detection ("SIEM-lite")

### Modern bar
**OWASP Top 10:2025 A09** ("Security Logging and Alerting Failures") explicitly calls for **append-only audit tables / integrity controls to prevent tampering or deletion**. **NIST 800-53r5 AU-9(3)** wants cryptographic integrity (signed hashes), AU-10 non-repudiation, AU-9 WORM/separate-system/read-only storage. **OCSF 1.8 (Mar 2026, Linux Foundation)** is the standard schema so events are SIEM/Sigma-consumable without bespoke parsing. Detections are managed **as code** (**Sigma** YAML, Git-reviewed, TP/FP-tested, mapped to MITRE ATT&CK). The realistic single-org tier is *one* OSS pipeline (Wazuh / OpenSearch / Loki+alerting; Falco only if containerized) plus append-only storage and a retention policy.

### Daedalus: STRONG
- Actually *has* a structured, owner-scoped, queryable audit log (the A09 baseline many tools fail).
- Already does **behavioral anomaly detection** whose rules map cleanly to real ATT&CK techniques (T1110 brute force, T1110.004 credential stuffing, mass deletion).
- Owner-only access aligns with AU-9 least privilege. **Not** shipping to an external SIEM is a legitimate single-org choice, not a deficiency.

### Daedalus: LAGS
1. **No tamper-evidence** — no hash-chaining, signing, append-only, or WORM. Fails OWASP A09:2025 + AU-9(3); if the DB is writable, logs can be silently altered (and an attacker who lands in a runner with DB creds could erase their tracks).
2. **Hand-rolled imperative detector**, not detections-as-code — no versioned rule files, TP/FP tests, or ATT&CK mapping; invisible rule drift.
3. **No standard schema** — bespoke fields require re-mapping for any future SIEM/Sigma use.
4. Retention/storage-integrity policy unstated.

### Concrete upgrade (proportionate — top-down, stop at "good enough")
- **Tier 1 (high value / low effort):** **hash-chain** the log (`hash = H(prev_hash || canonical_row)` per row, ~20 lines, no new infra) → satisfies A09/AU-9(3) intent. **Revoke UPDATE/DELETE** on the audit table from the app role (insert-only). Optionally periodically sign/anchor the chain head to a second store. Write a one-paragraph retention policy.
- **Tier 2:** refactor anomaly rules into **declarative, versioned, tested** definitions with ATT&CK IDs in metadata (adopt Sigma's *discipline*); adopt **OCSF field names** for the event shape.
- **Tier 3 (only if scope grows):** optional OCSF/syslog **export** so an operator *can* forward to Wazuh.
- **Explicitly DON'T** stand up full Wazuh/Elastic, SOAR, per-host agents, or HSM signing — gold-plating for single-org.

### Sources
- OWASP Top 10:2025 A09 (append-only / integrity vs tampering) — https://owasp.org/Top10/2025/A09_2025-Security_Logging_and_Alerting_Failures/
- NIST 800-53r5 AU-9(3) signed-hash integrity — https://csf.tools/reference/nist-sp-800-53/r5/au/au-9/au-9-3/ ; AU-9 WORM/read-only — https://csf.tools/reference/nist-sp-800-53/r5/au/au-9/ ; SP 800-92r1 draft — https://csrc.nist.gov/pubs/sp/800/92/r1/ipd
- OWASP logging cheat sheet — https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- OCSF — https://ocsf.io/ ; releases (1.8.0 Mar 2026) — https://github.com/ocsf/ocsf-schema/releases
- Sigma — https://github.com/SigmaHQ/sigma ; Falco — https://falco.org/ ; detection-as-code discipline — https://blog.nviso.eu/2025/07/17/detection-engineering-practicing-detection-as-code-repository-part-2/

---

## 6. Supply chain / connector signing

### Modern bar
Sign loadable artifacts with **Sigstore/cosign** (keyless: Fulcio short-lived cert + OIDC identity, witnessed in **Rekor**; verify with `cosign verify-blob` pinning `--certificate-identity` + `--certificate-oidc-issuer`; ship a **bundle** for offline verify; **sign by digest, not path**). Attach **SLSA provenance** (**v1.2**; Build L1 = provenance exists, L2 = hosted platform signs it, L3 = isolated/non-forgeable build) as an **in-toto** attestation. Enforce **capability/permission manifests** (browser MV3 pattern: declare permissions/host patterns ahead of time, re-consent on escalation). For tool-feeding plugins, **OWASP MCP Top 10 MCP03 (Tool Poisoning)** prescribes signing tool schemas, content-addressable hash versioning, and human approval for high-impact ops.

### Daedalus: STRONG
- Self-hosted, single-org, on-disk packs — **no public registry**, removing 2025–26's biggest vector (poisoned MCP registries).
- Connectors **already declare egress allowlists** — conceptually a capability manifest, the hardest cultural part.
- Single OIDC identity domain makes keyless signing trivial to map.

### Daedalus: LAGS
1. **No verify-on-load = SLSA L0.** Anyone/anything that can write a pack to disk (compromised CI, dependency, arbitrary-write bug, **a rogue agent run with filesystem access**) gets **arbitrary code execution at hot-reload time** — and hot-reload makes it *worse* (no deploy gate, no restart; write-to-disk *is* the exploit).
2. **No provenance** — can't answer "which build/commit/author produced this pack."
3. Egress allowlist is **declared but its integrity is unverified** — a tampered pack rewrites its own allowlist.
4. **No content-addressable pinning** — loads "whatever is at this path."
5. **Tool-definition rug-pull surface** (CVE-2025-54136 "MCPoison" class) — trivial with unsigned hot-reload.

### Concrete upgrade (priority order)
1. **Verify-on-load, fail-closed** — `cosign verify-blob` against the pack digest, pinning org signer identity + OIDC issuer; refuse on missing/invalid signature; ship a Sigstore **bundle** for offline verify. This single change exits L0 and closes the RCE-on-write hole.
2. **Sign packs in CI; target SLSA Build L2 now, L3 next**; verify builder + source commit at load.
3. **Content-address** the pack — load "signed digest X," not a path.
4. **Enforce** the egress allowlist as part of the **signed** manifest (deny-by-default); diff capability changes on reload and require re-approval on escalation.
5. **Human-in-the-loop + audit** every loaded version (author, signer, digest, timestamp) — extend the existing audit log.
6. If connectors feed an agent/LLM, treat tool descriptions as untrusted: pin tool-definition hashes; alert on post-approval mutation.

### Sources
- SLSA v1.2 build requirements — https://slsa.dev/spec/v1.2/build-requirements
- cosign verify (identity/issuer pinning, offline bundles) — https://docs.sigstore.dev/cosign/verifying/verify/ ; keyless signing — https://docs.sigstore.dev/cosign/signing/overview/ ; Rekor v2 GA — https://blog.sigstore.dev/rekor-v2-ga/
- in-toto (CNCF graduated) — https://in-toto.io/ ; attestation format — https://github.com/in-toto/attestation
- Chrome MV3 declared permissions (capability-manifest pattern) — https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions
- OWASP MCP03 Tool Poisoning — https://owasp.org/www-project-mcp-top-10/2025/MCP03-2025–Tool-Poisoning ; PoC — https://policylayer.com/attacks/hidden-instructions-in-tool-descriptions ; CVE-2025-54136 rug-pull — https://www.truefoundry.com/blog/blog-mcp-tool-poisoning-gateway-defense

---

## 7. Self-hosted operability (backup/restore, rotation, migrations, observability, DR)

### 7.1 Backup & restore
**Bar:** 3-2-1 has evolved to **3-2-1-1-0** (3 copies, 2 media, 1 offsite, **1 immutable/air-gapped**, **0 errors verified by automated restore testing**). For Postgres: **PITR via continuous WAL archiving** managed by **pgBackRest/Barman**, encrypted, on **object-lock/WORM** storage, with **failure alerting**.
**STRONG:** backups already land in MinIO (S3-compatible offsite-equivalent target); pre-yolo git snapshots add a recovery point.
**LAGS:** almost certainly **logical dumps, not PITR** (RPO = since-last-dump); **no object-lock immutability** (biggest ransomware/rogue-agent gap given the yolo threat model); **no automated restore verification** (the "0" is missing); backups colocated on the same host/MinIO (violates offsite); no backup-failure alerting.
**Upgrade:** adopt **pgBackRest + WAL archiving** (RPO hours → seconds); enable **MinIO Object Lock (Governance/Compliance)** on the backup bucket with a **separate least-privileged backup account**; add a **scheduled automated restore test** (restore + `pg_verifybackup` + smoke query, alert on failure); replicate one copy **off-host**; alert on backup age / WAL lag.

### 7.2 Secret rotation
**Bar (OWASP):** automated rotation via a secrets pipeline; best-in-class is **dynamic secrets**; otherwise the **dual-credential (alpha/beta) window** for zero-downtime rotation; **break-glass** in a secondary system, **tested routinely**; auto-renewed internal CA/TLS.
**STRONG:** Caddy likely auto-renews TLS leaf certs (ACME); small knowable consumer set.
**LAGS:** no evident rotation policy/automation for DB creds, MinIO keys, internal secrets (likely static, in env/compose — exactly what a yolo run could exfiltrate); no documented/tested break-glass; no internal-CA rotation story.
**Upgrade:** lightweight secrets manager (single-node Vault / SOPS+age / Infisical); **dual-credential window** for Postgres; scheduled rotation of MinIO backup account + DB password; written + tested break-glass runbook (sealed emergency superuser/root in a secondary store, quarterly test); automate internal-CA issuance/renewal (step-ca short-lived certs).

### 7.3 Upgrade / migration safety
**Bar:** **expand/contract (parallel change)** — every migration backward-compatible with the running app so old/new code coexist and rollback is safe; CI runs `alembic upgrade head` (and downgrade) on a fresh DB per PR and **fails if `alembic heads` > 1**; non-blocking DDL (`CREATE INDEX CONCURRENTLY`, add-nullable-then-backfill).
**STRONG:** already on Alembic; team is actively fixing migration issues.
**LAGS:** history is the smoking gun — **"4 alembic heads"** and **"missing tables mid-refactor"** = no CI gate for multiple heads / un-applyable migrations, schema/code coupled (no expand/contract), no migration testing on a prod-like DB.
**Upgrade (highest value, trivial):** CI step that applies migrations on a clean DB and **asserts exactly one head** (`alembic heads | wc -l`), plus a pre-commit hook — would have prevented the "4 heads" incident. Adopt **expand/contract discipline** (never drop/rename a column in the release that stops using it); test both upgrade and downgrade; take a backup immediately before each migration (ties to PITR).

### 7.4 Observability completeness
**Bar:** **Four Golden Signals** (latency/traffic/errors/saturation) + RED/USE; **alert on symptoms not causes**; **SLO/error-budget burn-rate** alerting; **dashboards are not alerts** — page, with a runbook link and a non-zero `for:`; explicitly cover the **boring-but-fatal** alerts (backup failure, cert expiry, disk full, WAL/replication lag); end-to-end tracing; synthetic/black-box health checks.
**STRONG:** Daedalus's best area — **Prometheus + Grafana + Loki + OpenTelemetry** is a current, textbook stack; signal collection and dashboards are present.
**LAGS:** the classic gap — **dashboards exist, alerting is thin/absent.** No defined SLOs/burn-rate alerts; the fatal-but-boring operational alerts likely missing (backup-too-old, cert-expiry, disk-full, WAL-stall); no clear alert-routing/on-call path (though a PushNotification + Gmail channel exists); partial trace coverage; no synthetic prober; undefined Loki retention.
**Upgrade:** Alertmanager rules for the **boring four** (backup-too-old, cert-expiry < 14d, disk-free < 15%, WAL-lag/Postgres-down) routed to the existing push/Gmail channel with runbook links; define 2–3 SLOs (login success, API latency, agent-run success) with burn-rate alerts; stand up a **synthetic prober** (Blackbox Exporter) hitting the real public/WebAuthn URL (would have caught the `rp_id` origin-bug class proactively); set explicit Loki retention (30–90d hot, longer for audit); audit OTel context propagation through runner containers + DB calls.

### 7.5 Disaster recovery
**Bar:** explicit **RTO/RPO** targets driving backup/replication design; **documented + tested DR runbook** proven via **game days**; eliminate SPOFs **including human ones**; environment **reproducible from code (IaC)** so you *rebuild* not just *restore*; map hidden deps (DNS, certs, identity).
**STRONG:** likely substantially compose-defined (most of the way to reproducible rebuild); git snapshots give a config recovery point; stack is small enough that full rebuild is feasible.
**LAGS:** **single host = dominant SPOF** (app + DB + MinIO + backups on one box → hardware loss is total loss); **no stated RTO/RPO**; no DR runbook and no game day (migration incidents suggest improvised recovery); **human SPOF** (single operator); drift risk if any host setup was done by hand.
**Upgrade:** write down RTO/RPO (e.g. RTO 4h, RPO 5min); get backups + one config snapshot **off-host**; author a DR runbook stored **outside** the host ("from blank machine: restore Postgres PITR → re-provision MinIO → compose up → re-point DNS/Caddy → restore secrets from break-glass"); **run one game day** (rebuild on a spare box, time against RTO, fix gaps); make the rebuild fully IaC (move any manual host steps into scripts); mitigate human SPOF via the runbook + a sealed break-glass.

### Sources
- 3-2-1-1-0 — https://www.avepoint.com/blog/backup/what-is-the-3-2-1-backup-rule ; https://www.veeam.com/blog/321-backup-rule.html
- Postgres continuous archiving & PITR — https://www.postgresql.org/docs/current/continuous-archiving.html ; pgBackRest workflow — https://dev.to/mohhddhassan/postgresql-backups-and-point-in-time-recovery-with-pgbackrest-13gp ; PITR-never-tested failure mode — https://www.jusdb.com/blog/postgresql-point-in-time-recovery-pitr
- MinIO Object Lock/immutability — https://docs.min.io/enterprise/aistor-object-store/administration/object-locking-and-immutability/ ; AWS S3 Object Lock — https://aws.amazon.com/s3/features/object-lock/ ; ransomware mitigation — https://blog.min.io/mitigating-ransomware-attacks-with-object-storage/
- OWASP Secrets Management Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html ; HashiCorp auto-rotation — https://developer.hashicorp.com/hcp/docs/vault-secrets/auto-rotation ; propagation/dual-credential — https://www.oasis.security/blog/stop-worrying-start-rotating
- Expand/contract — https://blogs.reliablepenguin.com/2025/11/16/database-migrations-without-drama-expand-contract-in-practice ; Alembic multiple heads — https://blog.jerrycodes.com/multiple-heads-in-alembic-migrations/ ; Alembic CI checks (pre-commit fail on >1 head, apply on fresh DB) — https://ldirer.com/blog/posts/practical-checks-alembic-migrations
- Google SRE — monitoring/golden signals/symptoms-over-causes — https://sre.google/sre-book/monitoring-distributed-systems/ ; Prometheus alerting — https://prometheus.io/docs/practices/alerting/ ; SRE alerting (runbook links, burn-rate) — https://incident.io/blog/sre-alerting-best-practices
- AWS DR whitepaper (RTO/RPO, IaC rebuild, drift) — https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-options-in-the-cloud.html ; cloud DR best practices (game days, human SPOF) — https://controlmonkey.io/blog/cloud-disaster-recovery-plan/

---

## Top 8 hardening / ops upgrades, ranked by risk-reduction impact

1. **Stop bind-mounting `~/.ssh` and `~/.claude` into runners; broker short-lived, scoped credentials per run.** Highest-leverage security change — today this is a one-step "injection → durable full-identity compromise" primitive (§3, §2).
2. **Wrap runners in gVisor `runsc` (after a quick seccomp + Landlock + rootless + drop-caps pass).** Closes the shared-host-kernel escape gap; moves runners from Tier 0 to Tier 2 (§1).
3. **Add an application-layer egress filtering proxy (domain/SNI/path allowlist) in front of the iptables backstop, and block raw Unix sockets.** Cuts a lethal-trifecta leg, defeats DNS/allowed-IP exfil (§1, §2).
4. **`cosign verify-blob` fail-closed on connector load + content-address packs + sign in CI (SLSA L2).** Closes the unsigned hot-reload RCE-on-write hole (§6).
5. **Backups: pgBackRest + WAL archiving (PITR), MinIO Object Lock with a separate backup account, one off-host copy, and an automated restore test with alerting.** Turns presumed backups into ransomware/rogue-agent-survivable, tested ones (§7.1, §7.5).
6. **Promote passkeys to primary (UV required, device-bound for admin), retire email OTP, and close the `X-Client-Cert-Fingerprint` spoofing gap** (network-isolate the app from non-Caddy paths + signed identity token) (§4).
7. **Quarantine untrusted content (CaMeL/Rule-of-Two) + human-in-the-loop approval for consequential tool calls + strip invisible Unicode from repo/web/config.** Architectural prompt-injection containment (§2).
8. **Ops hygiene bundle: hash-chain + insert-only audit log; CI gate asserting a single Alembic head + expand/contract migrations; Alertmanager rules for the "boring four" (backup age, cert expiry, disk full, WAL lag); written + game-day-tested RTO/RPO DR runbook.** Tamper-evidence, prevents the recurring migration incidents, and makes recovery real (§5, §7.3, §7.4, §7.5).
