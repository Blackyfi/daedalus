"""Pythia — subscription oracle.

Probes `claude /status` (with the same `~/.claude` auth state the connectors
use) and caches plan tier + remaining quota in Redis so the API can serve a
fast snapshot to the dashboard. See project-plan.md §6.10.
"""
from daedalus.pythia.probe import (
    SUBSCRIPTION_REDIS_KEY,
    SubscriptionInfo,
    probe_and_cache,
    probe_claude,
    read_cached,
)

__all__ = [
    "SUBSCRIPTION_REDIS_KEY",
    "SubscriptionInfo",
    "probe_and_cache",
    "probe_claude",
    "read_cached",
]
