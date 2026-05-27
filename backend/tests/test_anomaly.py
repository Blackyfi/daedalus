"""Audit-log anomaly detection rules (pure logic)."""
from __future__ import annotations

from types import SimpleNamespace

from daedalus.anomaly import detect


def _ev(action, *, ip=None, user_id=None, target_id=None):
    return SimpleNamespace(
        action=action, actor_ip=ip, actor_user_id=user_id, target_id=target_id
    )


# Generous defaults so each test only trips the rule it's exercising.
_KW = dict(
    window_minutes=10,
    ip_failure_threshold=5,
    cert_mismatch_threshold=5,
    account_ip_spread_threshold=3,
    bulk_delete_threshold=4,
)


def test_quiet_window_fires_nothing() -> None:
    events = [
        _ev("auth.login", ip="10.0.0.1"),
        _ev("auth.password_fail", ip="10.0.0.1"),  # 1 < 5
        _ev("task.delete", ip="10.0.0.1", user_id="u1"),
    ]
    assert detect(events, **_KW) == []


def test_ip_auth_failure_burst() -> None:
    events = [_ev("auth.password_fail", ip="1.2.3.4") for _ in range(5)]
    hits = detect(events, **_KW)
    assert [h.rule for h in hits if h.rule == "ip_auth_failures"]
    hit = next(h for h in hits if h.rule == "ip_auth_failures")
    assert hit.subject == "1.2.3.4"
    assert hit.subject_kind == "ip"
    assert hit.count == 5
    assert hit.detail["actions"] == {"auth.password_fail": 5}


def test_ip_burst_mixes_failure_kinds_but_ignores_successes() -> None:
    events = [
        _ev("auth.password_fail", ip="9.9.9.9"),
        _ev("auth.otp_fail", ip="9.9.9.9"),
        _ev("auth.totp_fail", ip="9.9.9.9"),
        _ev("auth.webauthn_fail", ip="9.9.9.9"),
        _ev("auth.password_fail", ip="9.9.9.9"),
        _ev("auth.login", ip="9.9.9.9"),  # success — not counted
        _ev("auth.password_ok", ip="9.9.9.9"),  # not a *_fail
    ]
    hit = next(h for h in detect(events, **_KW) if h.rule == "ip_auth_failures")
    assert hit.count == 5


def test_ip_failures_spread_across_ips_do_not_trip_ip_rule() -> None:
    events = [_ev("auth.password_fail", ip=f"10.0.0.{i}") for i in range(5)]
    assert [h for h in detect(events, **_KW) if h.rule == "ip_auth_failures"] == []


def test_cert_mismatch_spike_is_global() -> None:
    events = [_ev("auth.cert_mismatch", ip=f"10.0.0.{i}") for i in range(5)]
    hit = next(h for h in detect(events, **_KW) if h.rule == "cert_mismatch_spike")
    assert hit.subject == "*"
    assert hit.subject_kind == "global"
    assert hit.count == 5
    assert len(hit.detail["source_ips"]) == 5


def test_account_failure_spread_counts_distinct_ips() -> None:
    # Same account (resolved user id), three different source IPs.
    events = [
        _ev("auth.password_fail", ip="1.1.1.1", user_id="acct-1"),
        _ev("auth.otp_fail", ip="2.2.2.2", user_id="acct-1"),
        _ev("auth.totp_fail", ip="3.3.3.3", user_id="acct-1"),
        _ev("auth.password_fail", ip="3.3.3.3", user_id="acct-1"),  # dup IP
    ]
    hit = next(h for h in detect(events, **_KW) if h.rule == "account_failure_spread")
    assert hit.subject == "acct-1"
    assert hit.count == 3  # distinct IPs, not events
    assert hit.detail["distinct_ips"] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]


def test_account_spread_falls_back_to_email_target_when_user_unknown() -> None:
    # Pre-auth password guessing: no user id yet, account == submitted email.
    events = [
        _ev("auth.password_fail", ip="1.1.1.1", target_id="victim@example.com"),
        _ev("auth.password_fail", ip="2.2.2.2", target_id="victim@example.com"),
        _ev("auth.password_fail", ip="3.3.3.3", target_id="victim@example.com"),
    ]
    hit = next(h for h in detect(events, **_KW) if h.rule == "account_failure_spread")
    assert hit.subject == "victim@example.com"
    assert hit.count == 3


def test_bulk_deletion_by_one_actor() -> None:
    events = [
        _ev("task.delete", user_id="u1"),
        _ev("project.delete", user_id="u1"),
        _ev("note.delete", user_id="u1"),
        _ev("connector.delete", user_id="u1"),
    ]
    hit = next(h for h in detect(events, **_KW) if h.rule == "bulk_deletion")
    assert hit.subject == "u1"
    assert hit.count == 4
    assert hit.severity == "medium"


def test_deletes_below_threshold_or_split_across_actors_are_quiet() -> None:
    events = [
        _ev("task.delete", user_id="u1"),
        _ev("task.delete", user_id="u2"),
        _ev("project.delete", user_id="u3"),
    ]
    assert [h for h in detect(events, **_KW) if h.rule == "bulk_deletion"] == []


def test_zero_threshold_disables_a_rule() -> None:
    events = [_ev("auth.password_fail", ip="1.2.3.4") for _ in range(50)]
    kw = {**_KW, "ip_failure_threshold": 0}
    assert [h for h in detect(events, **kw) if h.rule == "ip_auth_failures"] == []


def test_message_is_human_readable() -> None:
    events = [_ev("auth.password_fail", ip="1.2.3.4") for _ in range(5)]
    hit = next(h for h in detect(events, **_KW) if h.rule == "ip_auth_failures")
    msg = hit.message(window_minutes=10)
    assert "ip 1.2.3.4" in msg
    assert "threshold 5" in msg
    assert "10m" in msg
