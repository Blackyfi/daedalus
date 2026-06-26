"""Transient verify-failure detection (IMPROVEMENTS #24)."""
from __future__ import annotations

import pytest

from daedalus.argus.verifier import is_transient_failure


@pytest.mark.parametrize(
    "text",
    [
        "curl: (6) Could not resolve host: pypi.org",
        "Connection reset by peer",
        "ETIMEDOUT while fetching deps",
        "HTTP 503 Service Unavailable",
        "error: 429 Too Many Requests",
        "context deadline exceeded",
    ],
)
def test_transient_signatures(text):
    assert is_transient_failure(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "AssertionError: expected 3 got 1",
        "2 failed, 5 passed",
        "SyntaxError: invalid syntax",
        "",
    ],
)
def test_real_failures_not_transient(text):
    assert is_transient_failure(text) is False
