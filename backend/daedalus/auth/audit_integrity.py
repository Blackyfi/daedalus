"""Tamper-evident hashing for audit entries (IMPROVEMENTS #15).

Each audit row carries an HMAC-SHA256 over its immutable fields, keyed by the
server pepper. An attacker who edits a row's action/actor/target/payload in the
DB can't recompute a matching hash without the pepper, so modification is
detectable after the fact (OWASP A09 / NIST AU-9). Verification is offline via
``verify_entry``. Kept dependency-free and pure so it's trivially unit-tested.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from daedalus.core.settings import get_settings


def _canonical(fields: dict[str, Any]) -> bytes:
    # Stable, sorted JSON so the hash is reproducible across processes.
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_entry_hash(
    *,
    action: str,
    actor_user_id: Any = None,
    actor_ip: str | None = None,
    actor_cert_fp: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    pepper: str | None = None,
) -> str:
    key = (pepper if pepper is not None else get_settings().password_pepper).encode()
    body = _canonical(
        {
            "action": action,
            "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
            "actor_ip": actor_ip,
            "actor_cert_fp": actor_cert_fp,
            "target_kind": target_kind,
            "target_id": target_id,
            "payload": payload or {},
        }
    )
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def verify_entry(entry_hash: str | None, **fields: Any) -> bool:
    """True iff `entry_hash` matches a freshly computed HMAC of `fields`."""
    if not entry_hash:
        return False
    return hmac.compare_digest(entry_hash, compute_entry_hash(**fields))
