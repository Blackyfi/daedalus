"""Per-project monthly cost-cap logic."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from daedalus.api.schemas import ProjectIn, ProjectPatch
from daedalus.costs import month_start, over_cap


def test_over_cap_none_means_unlimited() -> None:
    assert over_cap(None, 10**12) is False


def test_over_cap_boundaries() -> None:
    assert over_cap(1_000_000, 999_999) is False
    assert over_cap(1_000_000, 1_000_000) is True   # reaching the cap blocks
    assert over_cap(1_000_000, 1_500_000) is True
    assert over_cap(0, 0) is True                    # a zero cap blocks all runs


def test_month_start_is_first_of_month_midnight_utc() -> None:
    ms = month_start(datetime(2026, 5, 26, 13, 37, 5, tzinfo=timezone.utc))
    assert (ms.year, ms.month, ms.day) == (2026, 5, 1)
    assert (ms.hour, ms.minute, ms.second, ms.microsecond) == (0, 0, 0, 0)
    assert ms.tzinfo == timezone.utc


def test_project_schema_accepts_cap_and_rejects_negative() -> None:
    p = ProjectIn(name="x", workspace_path="/w", monthly_cost_cap_usd_micros=5_000_000)
    assert p.monthly_cost_cap_usd_micros == 5_000_000
    assert ProjectIn(name="x", workspace_path="/w").monthly_cost_cap_usd_micros is None
    with pytest.raises(ValidationError):
        ProjectIn(name="x", workspace_path="/w", monthly_cost_cap_usd_micros=-1)
    # patch accepts it too
    assert ProjectPatch(monthly_cost_cap_usd_micros=2_000_000).monthly_cost_cap_usd_micros == 2_000_000
