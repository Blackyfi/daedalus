"""Argon2id hashing with a server-side pepper. (§10.2)"""
from __future__ import annotations

import hmac
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from daedalus.core.settings import get_settings

# m=64MiB, t=3, p=4 — the recommended defaults in §10.2.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)


def _pepper(password: str) -> str:
    pepper = get_settings().password_pepper.encode()
    return hmac.new(pepper, password.encode(), sha256).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(_pepper(password))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, _pepper(password))
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
