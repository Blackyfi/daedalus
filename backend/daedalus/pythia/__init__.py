"""Pythia — subscription oracle.

Probes `claude /status` (with the same `~/.claude` auth state the connectors
use) and caches plan tier + remaining quota in Redis so the API can serve a
fast snapshot to the dashboard. See project-plan.md §6.10.
"""
from daedalus.pythia.probe import (
    SubscriptionInfo,
    probe_claude,
    probe_and_cache,
    read_cached,
    SUBSCRIPTION_REDIS_KEY,
)

__all__ = [
    "SubscriptionInfo",
    "probe_claude",
    "probe_and_cache",
    "read_cached",
    "SUBSCRIPTION_REDIS_KEY",
]
