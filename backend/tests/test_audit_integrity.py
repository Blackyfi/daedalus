"""Tamper-evident audit-entry hashing (IMPROVEMENTS #15)."""
from __future__ import annotations

from daedalus.auth.audit_integrity import compute_entry_hash, verify_entry

FIELDS = dict(
    action="project.delete",
    actor_user_id="11111111-1111-1111-1111-111111111111",
    actor_ip="10.0.0.1",
    actor_cert_fp="aa:bb",
    target_kind="project",
    target_id="22222222-2222-2222-2222-222222222222",
    payload={"name": "demo"},
    pepper="test-pepper",
)


def test_hash_is_deterministic():
    assert compute_entry_hash(**FIELDS) == compute_entry_hash(**FIELDS)


def test_hash_changes_when_any_field_changes():
    base = compute_entry_hash(**FIELDS)
    assert compute_entry_hash(**{**FIELDS, "action": "project.create"}) != base
    assert compute_entry_hash(**{**FIELDS, "payload": {"name": "evil"}}) != base
    assert compute_entry_hash(**{**FIELDS, "target_id": "33333333-3333-3333-3333-333333333333"}) != base


def test_verify_entry_roundtrip():
    h = compute_entry_hash(**FIELDS)
    assert verify_entry(h, **FIELDS) is True
    assert verify_entry(h, **{**FIELDS, "action": "tampered"}) is False
    assert verify_entry(None, **FIELDS) is False


def test_pepper_matters():
    a = compute_entry_hash(**FIELDS)
    b = compute_entry_hash(**{**FIELDS, "pepper": "other-pepper"})
    assert a != b
