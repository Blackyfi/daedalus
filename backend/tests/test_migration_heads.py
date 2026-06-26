"""Guard against the recurring multiple-Alembic-heads incident (IMPROVEMENTS #22).

A pure parse of alembic/versions: collect every revision and down_revision and
assert there is exactly one head (a revision no other revision descends from).
Running this in CI fails a PR that adds a second head before it reaches prod.
"""
from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_REV = re.compile(r"^revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN = re.compile(r"^down_revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def test_single_alembic_head():
    revisions: set[str] = set()
    down_revisions: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        m = _REV.search(text)
        if not m:
            continue
        revisions.add(m.group(1))
        d = _DOWN.search(text)
        if d:
            down_revisions.add(d.group(1))

    assert revisions, "no migrations found"
    heads = revisions - down_revisions
    assert len(heads) == 1, f"expected exactly one Alembic head, found {sorted(heads)}"
