"""TOTP (RFC 6238) helpers + recovery codes."""
from __future__ import annotations

import hashlib
import secrets

import pyotp

from daedalus.core.settings import get_settings

ISSUER = "Daedalus"


def new_totp_secret() -> str:
    return pyotp.random_base32(length=32)


def provisioning_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    if not secret or not code or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


# --- recovery codes ---

def generate_recovery_codes(n: int = 10) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    pepper = get_settings().password_pepper.encode()
    return hashlib.blake2b(code.encode(), key=pepper, digest_size=32).hexdigest()


def consume_recovery_code(stored_hashes: list[str], submitted: str) -> tuple[bool, list[str]]:
    h = hash_recovery_code(submitted.strip().lower())
    if h not in stored_hashes:
        return False, stored_hashes
    return True, [c for c in stored_hashes if c != h]
