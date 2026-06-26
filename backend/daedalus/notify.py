"""Outbound operator notifications for blocking/attention events (IMPROVEMENTS #17).

Fire-and-forget: posts a small JSON envelope to ``NOTIFY_WEBHOOK_URL`` (Slack/
Discord/generic) for events the operator opted into via ``NOTIFY_EVENTS``.
Disabled (no-op) when no webhook is configured, and it NEVER raises — a
notification failure must never affect the run pipeline or the scheduler.

Known event keys: ``needs_fixes``, ``run_failed``, ``rate_limit_pause``,
``anomaly``, ``merge_conflict``.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from daedalus.core.settings import get_settings

log = structlog.get_logger()


def enabled_events() -> set[str]:
    raw = get_settings().notify_events or ""
    return {e.strip() for e in raw.split(",") if e.strip()}


def should_notify(event: str) -> bool:
    """True when a webhook is configured and `event` is opted in (empty
    NOTIFY_EVENTS means 'all events')."""
    if not get_settings().notify_webhook_url:
        return False
    allow = enabled_events()
    return not allow or event in allow


def build_payload(event: str, summary: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "daedalus",
        "event": event,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(),
        **fields,
    }


async def _post(url: str, body: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=body)
    except Exception as exc:
        log.warning("notify.post_failed", event=body.get("event"), error=str(exc))


def emit(event: str, summary: str, **fields: Any) -> None:
    """Schedule a best-effort notification. Safe to call from any async context;
    never raises and never blocks the caller."""
    try:
        if not should_notify(event):
            return
        url = get_settings().notify_webhook_url
        body = build_payload(event, summary, fields)
        asyncio.get_running_loop().create_task(_post(url, body))
    except RuntimeError:
        # No running loop (sync context) — run the post to completion instead.
        try:
            asyncio.run(_post(get_settings().notify_webhook_url or "", body))
        except Exception as exc:
            log.warning("notify.emit_failed", event=event, error=str(exc))
    except Exception as exc:
        log.warning("notify.emit_failed", event=event, error=str(exc))
