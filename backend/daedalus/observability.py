"""Prometheus metrics shared by the API + workers."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Hermes / queue
QUEUE_DEPTH = Gauge(
    "daedalus_queue_depth",
    "Number of jobs waiting in the Hermes queue, by lane.",
    labelnames=("lane",),
)
RUNS_TOTAL = Counter(
    "daedalus_runs_total",
    "Total runs started, by kind.",
    labelnames=("kind",),
)
RUNS_COMPLETED_TOTAL = Counter(
    "daedalus_runs_completed_total",
    "Total runs that reached a terminal state, by kind and outcome.",
    labelnames=("kind", "state"),
)
RUN_DURATION_SECONDS = Histogram(
    "daedalus_run_duration_seconds",
    "Wall-clock duration of completed runs, by kind.",
    labelnames=("kind",),
    buckets=(1, 5, 15, 60, 300, 900, 1800, 3600),
)

# Argus
ARGUS_VERDICTS_TOTAL = Counter(
    "daedalus_argus_verdicts_total",
    "Argus verdicts emitted, by outcome.",
    labelnames=("verdict",),
)

# Auth
AUTH_LOGIN_TOTAL = Counter(
    "daedalus_auth_login_total",
    "Login attempts by outcome.",
    labelnames=("outcome", "factor"),
)
