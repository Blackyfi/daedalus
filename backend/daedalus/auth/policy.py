"""Password policy: length, character classes, simple zxcvbn-ish heuristic.

We don't ship a full zxcvbn; we approximate with class-counting + a small bad-list.
For HIBP/breach checks, wire up `daedalus check-passwords-against` offline.
"""
from __future__ import annotations

import re

# Smallest allowed length; §10.2 says 14.
MIN_LEN = 14


COMMON_BAD = {
    "password", "12345678", "qwerty", "letmein", "iloveyou", "admin",
    "welcome", "changeme", "daedalus", "passw0rd",
}


def policy_violations(password: str) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_LEN:
        problems.append(f"must be at least {MIN_LEN} characters")
    classes = sum([
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d",    password)),
        bool(re.search(r"\W|_", password)),
    ])
    if classes < 4:
        problems.append("must contain lowercase, uppercase, digit, and symbol")
    if password.lower() in COMMON_BAD:
        problems.append("password is in the common-passwords list")
    return problems
