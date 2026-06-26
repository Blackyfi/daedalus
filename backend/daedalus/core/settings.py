"""Runtime configuration. Pulled from env vars; cached."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, populate_by_name=True
    )

    # --- core ---
    public_url: str = Field("https://daedalus.your.lan", alias="DAEDALUS_PUBLIC_URL")
    role: str = Field("api", alias="ROLE")  # api | iris | hermes | talos | argus

    # --- db / redis / s3 ---
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")
    s3_endpoint: str = Field("http://minio:9000", alias="S3_ENDPOINT")
    s3_access_key: str = Field("daedalus", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field("changeme-minio", alias="S3_SECRET_KEY")
    minio_bucket: str = Field("daedalus", alias="MINIO_BUCKET")
    objects_dir: str = Field("/tmp/daedalus-objects", alias="DAEDALUS_OBJECTS_DIR")

    # --- auth ---
    session_secret: str = Field(..., alias="SESSION_SECRET")
    password_pepper: str = Field(..., alias="PASSWORD_PEPPER")
    session_idle_minutes: int = Field(30, alias="SESSION_IDLE_MINUTES")
    session_hard_hours: int = Field(12, alias="SESSION_HARD_HOURS")
    lockout_threshold: int = Field(5, alias="LOCKOUT_THRESHOLD")
    lockout_minutes: int = Field(15, alias="LOCKOUT_MINUTES")
    ip_ban_threshold: int = Field(25, alias="IP_BAN_THRESHOLD")
    ip_ban_minutes: int = Field(60, alias="IP_BAN_MINUTES")
    # Application-level encryption key for the TOTP secret at rest. When unset,
    # a key is derived deterministically from password_pepper (see auth.totp).
    totp_enc_key: str | None = Field(None, alias="TOTP_ENC_KEY")
    # When True (legacy / multi-org deployments) the API requires
    # `X-Client-Cert-Fingerprint` from the reverse proxy and binds sessions
    # to it. When False (self-hosted single-org behind Tailscale + Caddy
    # `tls internal`) sessions bind to a synthetic sentinel — auth still
    # goes through password + email OTP + TOTP/WebAuthn.
    require_client_cert: bool = Field(True, alias="REQUIRE_CLIENT_CERT")

    # --- smtp ---
    smtp_host: str = Field("localhost", alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_user: str | None = Field(None, alias="SMTP_USER")
    smtp_password: str | None = Field(None, alias="SMTP_PASSWORD")
    smtp_from: str = Field("daedalus@localhost", alias="SMTP_FROM")
    smtp_tls: bool = Field(True, alias="SMTP_TLS")

    # --- talos ---
    workspaces_root: str = Field("/workspaces", alias="TALOS_WORKSPACES_ROOT")
    connectors_dir: str = Field("/etc/daedalus/connectors", alias="TALOS_CONNECTORS_DIR")

    # uid/gid that talos+argus-worker run as. The api/hermes containers run as
    # root and create worktrees, so they must chown the result to this uid so
    # the agent (running under it) can write to its own worktree.
    agent_uid: int | None = Field(1000, alias="DAEDALUS_AGENT_UID")
    agent_gid: int | None = Field(1000, alias="DAEDALUS_AGENT_GID")

    # --- hermes scheduling ---
    # Global ceiling on concurrent runs across all projects (one Daedalus-managed
    # run per project; the cap limits how many *projects* can run at once).
    # Tune to your host's CPU/RAM headroom. With Pro Max-x20 + a safelock fallback
    # the meaningful limit is the box, not the API quota.
    max_concurrent_projects: int = Field(4, alias="MAX_CONCURRENT_PROJECTS")
    # How often the scheduler scans queues for claimable work, in seconds.
    scheduler_poll_seconds: float = Field(0.5, alias="SCHEDULER_POLL_SECONDS")
    # Project-lease TTL is connector wall-clock + this grace window.
    project_lease_grace_seconds: int = Field(300, alias="PROJECT_LEASE_GRACE_SECONDS")
    # Heartbeat interval for refreshing the project lease while a run is active.
    project_lease_heartbeat_seconds: int = Field(60, alias="PROJECT_LEASE_HEARTBEAT_SECONDS")
    # Wall-clock budget for Talos to drain in-flight runs on SIGTERM. Each
    # active PTY gets killed and its worker thread runs `_complete_run` →
    # cleanup, which deletes `hermes:lock:<rid>` so Hermes' bookkeeper can
    # finalize the run on its next tick. Must be < the docker-compose
    # `stop_grace_period` for the talos service or the kernel SIGKILLs us
    # mid-cleanup. 45s leaves ~15s headroom against a 60s grace.
    talos_shutdown_drain_seconds: int = Field(45, alias="TALOS_SHUTDOWN_DRAIN_SECONDS")

    # Hermes bookkeeper runs `git worktree prune` per project at this cadence
    # to keep `.git/worktrees/` admin entries from accumulating across run
    # lifecycles. 5 minutes is fine — the operation is cheap and idempotent.
    worktree_prune_interval_seconds: int = Field(300, alias="WORKTREE_PRUNE_INTERVAL_SECONDS")

    # --- audit-log anomaly detection (§15 phase 6) ---
    # A throttled scan in the Hermes bookkeeper flags threshold-crossing
    # patterns in the recent audit window and records them as
    # `anomaly.detected` audit events (visible in the owner audit UI). Each
    # threshold of 0 disables its rule. Cooldown keeps a standing condition
    # from re-firing every scan.
    anomaly_detection_enabled: bool = Field(True, alias="ANOMALY_DETECTION_ENABLED")
    anomaly_scan_interval_seconds: int = Field(120, alias="ANOMALY_SCAN_INTERVAL_SECONDS")
    anomaly_window_minutes: int = Field(10, alias="ANOMALY_WINDOW_MINUTES")
    anomaly_cooldown_minutes: int = Field(30, alias="ANOMALY_COOLDOWN_MINUTES")
    # Auth failures from one source IP within the window (brute force).
    anomaly_ip_failure_threshold: int = Field(15, alias="ANOMALY_IP_FAILURE_THRESHOLD")
    # Pinned-cert mismatches across the deployment within the window.
    anomaly_cert_mismatch_threshold: int = Field(5, alias="ANOMALY_CERT_MISMATCH_THRESHOLD")
    # Distinct source IPs failing auth against a single account (cred stuffing).
    anomaly_account_ip_spread_threshold: int = Field(4, alias="ANOMALY_ACCOUNT_IP_SPREAD_THRESHOLD")
    # `*.delete` actions by one actor within the window (mass destruction).
    anomaly_bulk_delete_threshold: int = Field(20, alias="ANOMALY_BULK_DELETE_THRESHOLD")

    # --- pythia (subscription oracle) ---
    pythia_refresh_seconds: int = Field(600, alias="PYTHIA_REFRESH_SECONDS")
    pythia_probe_timeout_seconds: float = Field(10.0, alias="PYTHIA_PROBE_TIMEOUT_SECONDS")
    pythia_cache_ttl_seconds: int = Field(1800, alias="PYTHIA_CACHE_TTL_SECONDS")

    # --- iris ---
    iris_port: int = Field(8001, alias="IRIS_PORT")

    # --- internal service-to-service ---
    # Workers (hermes/talos) reach the API container directly over backnet,
    # not via the public mTLS URL.
    internal_api_base: str = Field("http://api:8000", alias="INTERNAL_API_BASE")
    # Bearer key for the worker→API internal routes (/api/internal/*). Kept
    # distinct from session_secret so leaking one doesn't compromise the other
    # (a session-signing secret should never double as a transmitted bearer
    # credential). Falls back to session_secret when unset, for compatibility.
    internal_api_key: str | None = Field(None, alias="INTERNAL_API_KEY")

    @property
    def internal_key(self) -> str:
        return self.internal_api_key or self.session_secret

    # --- llm (used by Argus + planning) ---
    # Backend selector:
    #   "cli"  → shell out to the local `claude` CLI (uses Pro/Max OAuth).
    #            Default — zero per-token cost on top of the subscription.
    #   "http" → speak the OpenAI /v1/chat/completions shape against
    #            LLM_BASE_URL (vLLM / NIM / OpenAI / LiteLLM proxy / etc.).
    llm_backend: str = Field("cli", alias="LLM_BACKEND")

    # HTTP backend config — ignored when LLM_BACKEND=cli.
    llm_base_url: str = Field("http://llm:8000/v1", alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(None, alias="LLM_API_KEY")
    llm_model: str = Field("Qwen/Qwen2.5-Coder-32B-Instruct", alias="LLM_MODEL")
    llm_verifier_model: str | None = Field(None, alias="LLM_VERIFIER_MODEL")
    llm_timeout_seconds: float = Field(120.0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_diff_chars: int = Field(60_000, alias="LLM_MAX_DIFF_CHARS")
    llm_max_log_chars: int = Field(20_000, alias="LLM_MAX_LOG_CHARS")

    # --- outbound notifications (IMPROVEMENTS #17) ---
    # Generic webhook (Slack/Discord/etc.); empty disables all notifications.
    notify_webhook_url: str | None = Field(None, alias="NOTIFY_WEBHOOK_URL")
    # Comma-separated opt-in events; empty string means "all".
    notify_events: str = Field(
        "needs_fixes,run_failed,rate_limit_pause,anomaly", alias="NOTIFY_EVENTS"
    )

    # --- connector signing (IMPROVEMENTS #14) — opt-in, fail-closed ---
    connector_signing_required: bool = Field(False, alias="CONNECTOR_SIGNING_REQUIRED")
    connector_signing_pubkey: str | None = Field(None, alias="CONNECTOR_SIGNING_PUBKEY")

    # --- forge integration (IMPROVEMENTS #7) — opt-in, off preserves air-gap ---
    forge_provider: str = Field("none", alias="FORGE_PROVIDER")  # none | github | gitlab
    forge_token: str | None = Field(None, alias="FORGE_TOKEN")
    forge_repo: str | None = Field(None, alias="FORGE_REPO")  # owner/repo or GitLab id
    forge_api_base: str = Field("https://api.github.com", alias="FORGE_API_BASE")

    # --- opt-in flags for the large/architectural items (default = current
    # behaviour; see docs/BACKLOG_PROGRESS.md). These ship the config surface;
    # enabling them is a documented, staging-validated step. ---
    # #6 parallel intra-project runs (1 = today's single-runner-per-project).
    max_runs_per_project: int = Field(1, alias="MAX_RUNS_PER_PROJECT")
    # #12 per-run short-lived credential broker (empty = mounted creds, today).
    cred_broker_url: str | None = Field(None, alias="CRED_BROKER_URL")
    # #23 allow a passkey to act as the primary factor (off = 3-step today).
    passkey_primary_enabled: bool = Field(False, alias="PASSKEY_PRIMARY_ENABLED")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
