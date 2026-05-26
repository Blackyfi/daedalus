"""TOTP (RFC 6238) helpers + recovery codes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from daedalus.core.settings import get_settings

ISSUER = "Daedalus"

# Prefix tagging an encrypted TOTP secret so we can tell it apart from a legacy
# plaintext base32 secret (base32 never contains ':').
_ENC_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    """Build the Fernet used to encrypt TOTP secrets at rest. Uses TOTP_ENC_KEY
    when set, else derives a stable key from password_pepper so the feature
    works without extra configuration. Rotating either invalidates stored
    secrets (recover with `daedalus reset-totp`)."""
    settings = get_settings()
    material = (settings.totp_enc_key or settings.password_pepper).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    """Encrypt a base32 TOTP secret for storage."""
    return _ENC_PREFIX + _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Return the plaintext base32 secret. Accepts both encrypted values
    (``enc:v1:…``) and legacy plaintext, so pre-existing rows keep working."""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext
    try:
        return _fernet().decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # wrong key / corrupt → verification fails closed


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(_ENC_PREFIX)


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
    # Constant-time membership test to avoid leaking a match via timing.
    matched = any(hmac.compare_digest(h, stored) for stored in stored_hashes)
    if not matched:
        return False, stored_hashes
    return True, [c for c in stored_hashes if not hmac.compare_digest(c, h)]
