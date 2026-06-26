"""Pythia probe + cache.

Calls the same Anthropic OAuth endpoints `claude` itself uses, with the
operator's OAuth access token pulled from `~/.claude/.credentials.json`:

  GET https://api.anthropic.com/api/oauth/profile
      → account.email, account.has_claude_pro, organization.rate_limit_tier
  GET https://api.anthropic.com/api/oauth/usage
      → five_hour.utilization + resets_at, seven_day.utilization + resets_at

Pythia merges the two into a `SubscriptionInfo` and caches it in Redis under
`daedalus:subscription:claude` with `pythia_cache_ttl_seconds`. The API
endpoint reads from the cache; it never blocks on a network call.

Tolerant of every failure mode — every degraded path produces a well-typed
`kind` value that the SPA can render meaningfully.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from daedalus.core.settings import get_settings

log = structlog.get_logger()


SUBSCRIPTION_REDIS_KEY = "daedalus:subscription:claude"

# Anthropic OAuth surface used by `claude` itself.
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Where the OAuth credentials live. Mirrors the Talos compose mount of
# the host's `~/.claude` into `/home/daedalus/.claude`.
DEFAULT_CREDENTIALS_PATHS: tuple[str, ...] = (
    "{home}/.claude/.credentials.json",
    "/home/daedalus/.claude/.credentials.json",
    "/root/.claude/.credentials.json",
)


# ── Public dataclass ─────────────────────────────────────────────────────────


@dataclass
class SubscriptionInfo:
    """Snapshot of the operator's Claude Code plan + quota.

    `kind` indicates the parser's confidence:
      - "ok"             : profile + usage both succeeded
      - "auth_required"  : credentials missing or expired (401)
      - "cli_missing"    : no credentials.json found (claude was never logged in)
      - "timeout"        : HTTP probe hung past `pythia_probe_timeout_seconds`
      - "unparsed"       : profile returned but not in the expected shape
      - "error"          : non-401 HTTP failure
    """

    kind: str = "unparsed"
    email: str | None = None
    plan: str | None = None  # display string (e.g. "Max 20x")
    plan_tier: str | None = None  # normalised: "pro" | "pro_max" | "max_5x" | "max_20x" | "unknown"
    weekly_used_pct: float | None = None
    five_hour_used_pct: float | None = None
    weekly_resets_in: str | None = None
    five_hour_resets_in: str | None = None
    raw_text: str = ""
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── Tier classification ──────────────────────────────────────────────────────

_RATE_LIMIT_TIER_MAP: dict[str, tuple[str, str]] = {
    "default_claude_max_20x": ("Max 20x", "max_20x"),
    "default_claude_max_5x":  ("Max 5x",  "max_5x"),
    "default_claude_max":     ("Claude Max", "max_5x"),
    "default_claude_pro":     ("Pro",     "pro"),
    "default_claude_team":    ("Team",    "team"),
    "default_claude_enterprise": ("Enterprise", "enterprise"),
}


def _classify_rate_limit_tier(tier: str | None) -> tuple[str | None, str | None]:
    if not tier:
        return (None, None)
    key = tier.lower()
    if key in _RATE_LIMIT_TIER_MAP:
        return _RATE_LIMIT_TIER_MAP[key]
    # Heuristic suffix match — keeps working if Anthropic adds new tiers.
    if "max_20x" in key:
        return ("Max 20x", "max_20x")
    if "max_5x" in key:
        return ("Max 5x", "max_5x")
    if "max" in key:
        return ("Claude Max", "max_5x")
    if "pro" in key:
        return ("Pro", "pro")
    if "team" in key:
        return ("Team", "team")
    if "enterprise" in key:
        return ("Enterprise", "enterprise")
    return (tier, "unknown")


# ── Reset countdown formatting ───────────────────────────────────────────────


def _format_resets_in(resets_at_iso: str | None) -> str | None:
    """Convert an ISO timestamp into 'Xd Yh' / 'Yh Zm' / 'Zm' string."""
    if not resets_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(resets_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = dt - datetime.now(UTC)
    secs = max(int(delta.total_seconds()), 0)
    if secs == 0:
        return "now"
    days, secs = divmod(secs, 86_400)
    hours, secs = divmod(secs, 3_600)
    mins, _ = divmod(secs, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# ── Credential lookup ────────────────────────────────────────────────────────


def _candidate_credentials_paths() -> list[Path]:
    home = os.environ.get("HOME", "")
    seen: set[Path] = set()
    out: list[Path] = []
    for tmpl in DEFAULT_CREDENTIALS_PATHS:
        p = Path(tmpl.format(home=home))
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _read_oauth_token() -> tuple[str | None, dict[str, Any]]:
    """Locate ~/.claude/.credentials.json and pull the OAuth access token.

    Returns (token, oauth_blob) or (None, {}). `oauth_blob` carries
    secondary fields (`subscriptionType`, `rateLimitTier`) that we use as a
    fallback if the profile endpoint is unreachable.
    """
    for path in _candidate_credentials_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        oauth = data.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if token:
            return (token, oauth)
    return (None, {})


# ── Parsers ──────────────────────────────────────────────────────────────────


def _apply_profile(info: SubscriptionInfo, profile: dict[str, Any]) -> None:
    account = profile.get("account") or {}
    organization = profile.get("organization") or {}
    info.email = account.get("email") or info.email
    tier = organization.get("rate_limit_tier") or organization.get("seat_tier")
    plan, plan_tier = _classify_rate_limit_tier(tier)
    if plan:
        info.plan = plan
    if plan_tier:
        info.plan_tier = plan_tier
    # Light fallbacks if rate_limit_tier is missing.
    if info.plan is None:
        if account.get("has_claude_max"):
            info.plan = "Claude Max"
            info.plan_tier = info.plan_tier or "max_5x"
        elif account.get("has_claude_pro"):
            info.plan = "Pro"
            info.plan_tier = info.plan_tier or "pro"


def _apply_usage(info: SubscriptionInfo, usage: dict[str, Any]) -> None:
    five = usage.get("five_hour") or {}
    seven = usage.get("seven_day") or {}
    if isinstance(five, dict):
        if five.get("utilization") is not None:
            info.five_hour_used_pct = float(five["utilization"])
        info.five_hour_resets_in = _format_resets_in(five.get("resets_at"))
    if isinstance(seven, dict):
        if seven.get("utilization") is not None:
            info.weekly_used_pct = float(seven["utilization"])
        info.weekly_resets_in = _format_resets_in(seven.get("resets_at"))


def _apply_oauth_blob_fallback(info: SubscriptionInfo, blob: dict[str, Any]) -> None:
    """Last-resort tier classification from the local credentials file."""
    if info.plan is not None:
        return
    tier = blob.get("rateLimitTier") or blob.get("subscriptionType")
    plan, plan_tier = _classify_rate_limit_tier(tier)
    if plan:
        info.plan = plan
    if plan_tier:
        info.plan_tier = plan_tier


# Optional CLI-output parser, retained as a backstop.
def parse_status_text(raw_text: str) -> SubscriptionInfo:
    """Parse `claude --print '/status'` output (when it works)."""
    info = SubscriptionInfo(raw_text=raw_text or "")
    if not raw_text:
        return info
    # If the slash-command path is unavailable, the CLI says so explicitly.
    if "/status isn't available" in raw_text or "isn't available in this environment" in raw_text:
        info.kind = "unparsed"
        info.error = "claude /status not available in --print mode"
        return info
    info.kind = "unparsed"
    return info


# ── Probe orchestration ──────────────────────────────────────────────────────


def probe_claude(*, timeout_seconds: float | None = None) -> SubscriptionInfo:
    """Read the OAuth credentials, call /api/oauth/{profile,usage}, return
    a SubscriptionInfo. Never raises."""
    settings = get_settings()
    timeout = timeout_seconds if timeout_seconds is not None else settings.pythia_probe_timeout_seconds

    token, oauth_blob = _read_oauth_token()
    if not token:
        return SubscriptionInfo(
            kind="cli_missing",
            error="no claudeAiOauth.accessToken in ~/.claude/.credentials.json",
        )

    info = SubscriptionInfo()
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "User-Agent": "daedalus-pythia/0.1 (claude-cli-compat)",
    }

    raw_chunks: list[str] = []
    profile_data: dict[str, Any] | None = None
    usage_data: dict[str, Any] | None = None

    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            # 1. Profile.
            try:
                resp = client.get(PROFILE_URL)
                raw_chunks.append(f"GET {PROFILE_URL} → {resp.status_code}")
                if resp.status_code == 401:
                    return SubscriptionInfo(
                        kind="auth_required",
                        error="OAuth token rejected — run `claude /login` on the host",
                    )
                if resp.status_code == 200:
                    profile_data = resp.json()
                    raw_chunks.append(json.dumps(profile_data)[:1000])
                else:
                    info.error = f"profile {resp.status_code}: {resp.text[:200]}"
            except httpx.TimeoutException:
                return SubscriptionInfo(kind="timeout", error="profile request hung")
            except httpx.HTTPError as e:
                info.error = f"profile error: {e!r}"

            # 2. Usage.
            try:
                resp = client.get(USAGE_URL)
                raw_chunks.append(f"GET {USAGE_URL} → {resp.status_code}")
                if resp.status_code == 200:
                    usage_data = resp.json()
                    raw_chunks.append(json.dumps(usage_data)[:1000])
                # 401 here was already handled above; ignore here so we still
                # surface profile-only data if usage is missing.
            except httpx.TimeoutException:
                # Don't fail the whole probe if just usage is slow.
                info.error = (info.error + " | " if info.error else "") + "usage timeout"
            except httpx.HTTPError as e:
                info.error = (info.error + " | " if info.error else "") + f"usage error: {e!r}"
    except Exception as e:
        return SubscriptionInfo(kind="error", error=f"unexpected probe error: {e!r}")

    if profile_data:
        _apply_profile(info, profile_data)
    if usage_data:
        _apply_usage(info, usage_data)
    if info.plan is None:
        _apply_oauth_blob_fallback(info, oauth_blob)

    info.raw_text = "\n".join(raw_chunks)[:4000]

    if profile_data and usage_data:
        info.kind = "ok"
    elif profile_data:
        info.kind = "ok" if info.plan else "unparsed"
    else:
        info.kind = info.kind if info.kind != "unparsed" else "error"
    return info


def probe_claude_cli_fallback() -> SubscriptionInfo:
    """Last resort: shell out to `claude --print '/status'` and try to parse.
    Useful only on hosts where the OAuth endpoints are blocked."""
    settings = get_settings()
    binary = shutil.which("claude")
    if not binary:
        return SubscriptionInfo(kind="cli_missing", error="claude CLI not on PATH")
    try:
        proc = subprocess.run(
            [binary, "--print", "/status"],
            capture_output=True,
            text=True,
            timeout=settings.pythia_probe_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return SubscriptionInfo(kind="timeout", error="claude /status hung")
    except Exception as e:
        return SubscriptionInfo(kind="error", error=f"cli error: {e!r}")
    info = parse_status_text((proc.stdout or "") + (proc.stderr or ""))
    return info


def probe_and_cache(redis_client) -> SubscriptionInfo:
    """Probe + write to Redis under SUBSCRIPTION_REDIS_KEY. Returns the info."""
    settings = get_settings()
    info = probe_claude()
    if info.kind not in {"ok", "auth_required"}:
        # Try the CLI fallback once.
        cli_info = probe_claude_cli_fallback()
        if cli_info.kind in {"ok", "auth_required"}:
            info = cli_info
    payload = json.dumps(asdict(info))
    try:
        redis_client.set(
            SUBSCRIPTION_REDIS_KEY,
            payload,
            ex=settings.pythia_cache_ttl_seconds,
        )
    except Exception:
        log.exception("pythia.cache_write_failed")
    log.info(
        "pythia.probe_complete",
        kind=info.kind,
        plan_tier=info.plan_tier,
        weekly_pct=info.weekly_used_pct,
        five_hour_pct=info.five_hour_used_pct,
    )
    return info


def read_cached(redis_client) -> SubscriptionInfo | None:
    """Return the cached snapshot, or None if absent / unreadable.

    Note: this is called from the API process which uses the *async* Redis
    client, so we await `.get()` properly.
    """
    import inspect

    try:
        result = redis_client.get(SUBSCRIPTION_REDIS_KEY)
        if inspect.iscoroutine(result):
            # We're being called sync from a sync caller (e.g. tests).
            # Asyncio's `run_until_complete` is the only safe option, but
            # callers in async contexts should use `read_cached_async`.
            raise RuntimeError(
                "read_cached called with an async redis client; "
                "use read_cached_async instead"
            )
        raw = result
    except Exception:
        log.exception("pythia.cache_read_failed")
        return None
    return _decode_cached(raw)


async def read_cached_async(redis_client) -> SubscriptionInfo | None:
    try:
        raw = await redis_client.get(SUBSCRIPTION_REDIS_KEY)
    except Exception:
        log.exception("pythia.cache_read_failed")
        return None
    return _decode_cached(raw)


def _decode_cached(raw) -> SubscriptionInfo | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return SubscriptionInfo(**{k: data.get(k) for k in SubscriptionInfo.__dataclass_fields__})
