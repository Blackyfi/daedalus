"""Audit-log anomaly detection (spec §15 phase 6).

A periodic scan over the recent `audit_events` window. Each rule groups the
events by some subject — a source IP, an actor/account, or the whole window —
and flags a subject whose count crosses a configurable threshold. A flagged
anomaly is written back into the audit log as an `anomaly.detected` event so it
surfaces in the existing owner-only audit UI; a per-(rule, subject) Redis
cooldown key keeps the same condition from re-firing on every scan tick.

The detector is deliberately rule-based and dependency-free: it reuses the
audit log we already write rather than standing up a separate alert store or
pulling in an ML dependency. The pure `detect` function takes plain event
records and explicit thresholds so it can be unit-tested without a DB/Redis;
`scan` is the thin async wrapper that reads the window, applies cooldowns, and
records the hits.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus import notify
from daedalus.auth import audit
from daedalus.core.settings import Settings
from daedalus.db.models import AuditEvent

logger = structlog.get_logger()

# The action we emit for a detected anomaly. Excluded from the scan window so
# anomaly events can never feed the detector (no self-amplifying loop).
ANOMALY_ACTION = "anomaly.detected"

# Auth-failure actions that signal credential guessing.
_AUTH_FAIL_ACTIONS = frozenset(
    {"auth.password_fail", "auth.otp_fail", "auth.totp_fail", "auth.webauthn_fail"}
)


class _Event(Protocol):
    """The subset of `AuditEvent` the rules read. Lets tests pass plain stubs."""

    action: str
    actor_ip: str | None
    actor_user_id: Any
    target_id: str | None


@dataclass(frozen=True)
class Anomaly:
    rule: str
    severity: str  # "medium" | "high"
    subject: str  # the grouping key value (an IP, an account id/email, or "*")
    subject_kind: str  # "ip" | "account" | "global"
    count: int
    threshold: int
    detail: dict[str, Any] = field(default_factory=dict)

    def message(self, window_minutes: int) -> str:
        where = "across the deployment" if self.subject_kind == "global" else f"from {self.subject_kind} {self.subject}"
        return (
            f"{self.count} {self.rule.replace('_', ' ')} {where} "
            f"in {window_minutes}m (threshold {self.threshold})"
        )


def _count_actions(events: Iterable[_Event]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for ev in events:
        out[ev.action] += 1
    return dict(out)


def _account_key(ev: _Event) -> str | None:
    """Stable per-account key for a failure event.

    Login failures carry the account either as `actor_user_id` (once the user
    row is resolved) or as `target_id` (the submitted email, on the password
    step before/when the user is unknown). Prefer the user id; fall back to the
    email so pre-auth password guessing is still attributable.
    """
    if getattr(ev, "actor_user_id", None):
        return str(ev.actor_user_id)
    return ev.target_id or None


def detect(
    events: Iterable[_Event],
    *,
    window_minutes: int,
    ip_failure_threshold: int,
    cert_mismatch_threshold: int,
    account_ip_spread_threshold: int,
    bulk_delete_threshold: int,
) -> list[Anomaly]:
    """Pure rule evaluation over a window of audit events.

    Returns every anomaly the rules fire on; cooldown/dedup is the caller's job.
    A threshold of 0 disables its rule (avoids flagging on an empty window).
    """
    events = list(events)
    hits: list[Anomaly] = []

    # Rule 1 — auth-failure burst from a single source IP (brute force).
    if ip_failure_threshold > 0:
        by_ip: dict[str, list[_Event]] = defaultdict(list)
        for ev in events:
            if ev.action in _AUTH_FAIL_ACTIONS and ev.actor_ip:
                by_ip[str(ev.actor_ip)].append(ev)
        for ip, evs in by_ip.items():
            if len(evs) >= ip_failure_threshold:
                hits.append(
                    Anomaly(
                        rule="ip_auth_failures",
                        severity="high",
                        subject=ip,
                        subject_kind="ip",
                        count=len(evs),
                        threshold=ip_failure_threshold,
                        detail={"actions": _count_actions(evs)},
                    )
                )

    # Rule 2 — pinned-cert mismatches (probing / a stolen-but-wrong cert).
    if cert_mismatch_threshold > 0:
        mismatches = [e for e in events if e.action == "auth.cert_mismatch"]
        if len(mismatches) >= cert_mismatch_threshold:
            ips = sorted({str(e.actor_ip) for e in mismatches if e.actor_ip})
            hits.append(
                Anomaly(
                    rule="cert_mismatch_spike",
                    severity="high",
                    subject="*",
                    subject_kind="global",
                    count=len(mismatches),
                    threshold=cert_mismatch_threshold,
                    detail={"source_ips": ips[:20]},
                )
            )

    # Rule 3 — one account hammered from many distinct IPs (credential stuffing).
    if account_ip_spread_threshold > 0:
        by_acct: dict[str, set[str]] = defaultdict(set)
        for ev in events:
            if ev.action in _AUTH_FAIL_ACTIONS and ev.actor_ip:
                key = _account_key(ev)
                if key:
                    by_acct[key].add(str(ev.actor_ip))
        for acct, ips in by_acct.items():
            if len(ips) >= account_ip_spread_threshold:
                hits.append(
                    Anomaly(
                        rule="account_failure_spread",
                        severity="high",
                        subject=acct,
                        subject_kind="account",
                        count=len(ips),
                        threshold=account_ip_spread_threshold,
                        detail={"distinct_ips": sorted(ips)[:20]},
                    )
                )

    # Rule 4 — bulk deletions by one actor (compromise / mass destruction).
    if bulk_delete_threshold > 0:
        by_actor: dict[str, list[_Event]] = defaultdict(list)
        for ev in events:
            if ev.action.endswith(".delete"):
                key = _account_key(ev) or (str(ev.actor_ip) if ev.actor_ip else None)
                if key:
                    by_actor[key].append(ev)
        for actor, evs in by_actor.items():
            if len(evs) >= bulk_delete_threshold:
                hits.append(
                    Anomaly(
                        rule="bulk_deletion",
                        severity="medium",
                        subject=actor,
                        subject_kind="account",
                        count=len(evs),
                        threshold=bulk_delete_threshold,
                        detail={"actions": _count_actions(evs)},
                    )
                )

    return hits


async def _claim_cooldown(redis, settings: Settings, anomaly: Anomaly) -> bool:
    """Atomically claim a per-(rule, subject) cooldown slot.

    Returns True only when this is the first sighting within the cooldown
    window (SET NX). Fails open on a Redis error so a flaky cache degrades to
    "report it" rather than "swallow it".
    """
    key = f"daedalus:anomaly:cooldown:{anomaly.rule}:{anomaly.subject}"
    ttl = max(1, settings.anomaly_cooldown_minutes * 60)
    try:
        return bool(await redis.set(key, "1", nx=True, ex=ttl))
    except Exception:
        logger.warning("anomaly_cooldown_redis_error", rule=anomaly.rule)
        return True


async def _record(db: AsyncSession, settings: Settings, anomaly: Anomaly) -> None:
    payload: dict[str, Any] = {
        "rule": anomaly.rule,
        "severity": anomaly.severity,
        "subject": anomaly.subject,
        "subject_kind": anomaly.subject_kind,
        "count": anomaly.count,
        "threshold": anomaly.threshold,
        "window_minutes": settings.anomaly_window_minutes,
        "message": anomaly.message(settings.anomaly_window_minutes),
        **anomaly.detail,
    }
    await audit.record(
        db,
        actor_ip=anomaly.subject if anomaly.subject_kind == "ip" else None,
        action=ANOMALY_ACTION,
        target_kind=anomaly.subject_kind,
        target_id=anomaly.subject,
        payload=payload,
    )


async def scan(
    db: AsyncSession,
    redis,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[Anomaly]:
    """Read the recent audit window, evaluate the rules, record fresh hits.

    Returns the anomalies that were newly recorded this tick (i.e. not still in
    their cooldown window). Callers should `commit` afterwards.
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(minutes=settings.anomaly_window_minutes)
    res = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.at >= window_start)
        .where(AuditEvent.action != ANOMALY_ACTION)
        .order_by(AuditEvent.at.asc())
    )
    events = list(res.scalars().all())

    candidates = detect(
        events,
        window_minutes=settings.anomaly_window_minutes,
        ip_failure_threshold=settings.anomaly_ip_failure_threshold,
        cert_mismatch_threshold=settings.anomaly_cert_mismatch_threshold,
        account_ip_spread_threshold=settings.anomaly_account_ip_spread_threshold,
        bulk_delete_threshold=settings.anomaly_bulk_delete_threshold,
    )

    fired: list[Anomaly] = []
    for anomaly in candidates:
        if await _claim_cooldown(redis, settings, anomaly):
            await _record(db, settings, anomaly)
            fired.append(anomaly)
            notify.emit(
                "anomaly",
                f"Security anomaly: {anomaly.rule} ({anomaly.severity}) on "
                f"{anomaly.subject_kind} {anomaly.subject}",
                rule=anomaly.rule,
                severity=anomaly.severity,
                subject=anomaly.subject,
                count=anomaly.count,
            )
    if fired:
        await db.flush()
        logger.info(
            "anomaly_scan_fired",
            count=len(fired),
            rules=sorted({a.rule for a in fired}),
        )
    return fired
