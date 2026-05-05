"""Pythia parsers — tier classification, OAuth profile/usage merge, edge cases."""
from __future__ import annotations

from daedalus.pythia.probe import (
    SubscriptionInfo,
    _apply_oauth_blob_fallback,
    _apply_profile,
    _apply_usage,
    _classify_rate_limit_tier,
    _format_resets_in,
)


def test_classify_known_tiers() -> None:
    assert _classify_rate_limit_tier("default_claude_max_20x") == ("Max 20x", "max_20x")
    assert _classify_rate_limit_tier("default_claude_max_5x") == ("Max 5x", "max_5x")
    assert _classify_rate_limit_tier("default_claude_pro") == ("Pro", "pro")
    assert _classify_rate_limit_tier("default_claude_team") == ("Team", "team")
    assert _classify_rate_limit_tier("default_claude_enterprise") == ("Enterprise", "enterprise")


def test_classify_heuristic_suffix() -> None:
    """If Anthropic adds a new tier name, the prefix-keyed map misses but the
    heuristic still finds Max-20x in `something_max_20x_v2`."""
    assert _classify_rate_limit_tier("something_max_20x_v2") == ("Max 20x", "max_20x")
    assert _classify_rate_limit_tier("alpha_pro_beta") == ("Pro", "pro")


def test_classify_unknown_keeps_label_marks_unknown() -> None:
    assert _classify_rate_limit_tier("future_tier") == ("future_tier", "unknown")


def test_classify_none_safe() -> None:
    assert _classify_rate_limit_tier(None) == (None, None)
    assert _classify_rate_limit_tier("") == (None, None)


def test_apply_profile_max_20x_with_email() -> None:
    info = SubscriptionInfo()
    _apply_profile(
        info,
        {
            "account": {
                "email": "ops@example.com",
                "has_claude_max": True,
                "has_claude_pro": False,
            },
            "organization": {
                "rate_limit_tier": "default_claude_max_20x",
                "subscription_status": "active",
            },
        },
    )
    assert info.email == "ops@example.com"
    assert info.plan == "Max 20x"
    assert info.plan_tier == "max_20x"


def test_apply_profile_falls_back_to_account_flags() -> None:
    """No rate_limit_tier on the org → fall back to has_claude_max / has_claude_pro."""
    info = SubscriptionInfo()
    _apply_profile(
        info,
        {"account": {"email": "x@y.io", "has_claude_max": True}, "organization": {}},
    )
    assert info.plan == "Claude Max"
    assert info.plan_tier == "max_5x"


def test_apply_usage_extracts_percentages_and_resets() -> None:
    info = SubscriptionInfo()
    _apply_usage(
        info,
        {
            "five_hour": {"utilization": 36.0, "resets_at": "2999-01-01T00:00:00+00:00"},
            "seven_day": {"utilization": 8.0, "resets_at": "2999-01-08T00:00:00+00:00"},
        },
    )
    assert info.five_hour_used_pct == 36.0
    assert info.weekly_used_pct == 8.0
    assert info.five_hour_resets_in is not None
    assert info.weekly_resets_in is not None


def test_apply_usage_handles_missing_buckets() -> None:
    info = SubscriptionInfo()
    _apply_usage(info, {"five_hour": {}, "seven_day": {}})
    assert info.five_hour_used_pct is None
    assert info.weekly_used_pct is None


def test_apply_oauth_blob_fallback_when_profile_missing() -> None:
    info = SubscriptionInfo()
    _apply_oauth_blob_fallback(info, {"rateLimitTier": "default_claude_max_20x"})
    assert info.plan_tier == "max_20x"


def test_format_resets_in_handles_iso_formats() -> None:
    # Far-future timestamp (well past today's date in tests).
    out = _format_resets_in("2999-01-08T00:00:00+00:00")
    assert out is not None
    assert "d" in out  # at least days component
    assert _format_resets_in(None) is None
    assert _format_resets_in("not-a-date") is None


def test_subscription_info_dataclass_contract_stable() -> None:
    """The SPA TypeScript interface mirrors these fields one-for-one."""
    fields = set(SubscriptionInfo.__dataclass_fields__)
    expected = {
        "kind",
        "email",
        "plan",
        "plan_tier",
        "weekly_used_pct",
        "five_hour_used_pct",
        "weekly_resets_in",
        "five_hour_resets_in",
        "raw_text",
        "error",
        "fetched_at",
    }
    assert expected.issubset(fields)
