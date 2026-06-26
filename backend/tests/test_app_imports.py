"""Smoke test: the FastAPI app imports and all route modules load.

Unit tests don't import the route modules, so a broken import there (e.g. a
symbol missing from a package __init__) would only crash at API boot. Importing
the app here turns that into a fast, deterministic test failure.
"""
from __future__ import annotations


def test_app_imports_and_registers_routes():
    # Importing the app exercises every route module — a broken import in any
    # router (the failure mode this test exists for) raises right here.
    from daedalus.main import app

    paths = set(app.openapi()["paths"])
    assert any(p.endswith("/merge-batches/{bid}/undo") for p in paths)
    assert any(p.endswith("/ci-failure") for p in paths)
    assert any(p.endswith("/runs/{rid}/argus") for p in paths)
