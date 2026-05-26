"""Coverage for the M1/M2/L1 hardening: TOTP-at-rest encryption, the dedicated
internal-service key, and constant-time recovery-code comparison."""
from __future__ import annotations

import pytest

from daedalus.auth import totp
from daedalus.core.settings import Settings, get_settings


def test_totp_secret_encrypt_decrypt_roundtrip() -> None:
    secret = totp.new_totp_secret()
    enc = totp.encrypt_secret(secret)
    assert enc.startswith("enc:v1:")
    assert enc != secret
    assert totp.is_encrypted(enc)
    assert totp.decrypt_secret(enc) == secret


def test_decrypt_passes_through_legacy_plaintext() -> None:
    legacy = totp.new_totp_secret()  # base32, no prefix
    assert not totp.is_encrypted(legacy)
    assert totp.decrypt_secret(legacy) == legacy


def test_encrypted_secret_still_verifies_a_live_code() -> None:
    pyotp = pytest.importorskip("pyotp")
    secret = totp.new_totp_secret()
    enc = totp.encrypt_secret(secret)
    code = pyotp.TOTP(secret).now()
    assert totp.verify_totp(totp.decrypt_secret(enc), code) is True


def test_corrupt_ciphertext_fails_closed() -> None:
    assert totp.decrypt_secret("enc:v1:not-a-valid-token") == ""


def test_internal_key_falls_back_to_session_secret() -> None:
    s = get_settings()
    assert s.internal_key == s.session_secret


def test_internal_key_prefers_dedicated(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "dedicated-internal-key-value")
    s = Settings()  # reads env directly
    assert s.internal_key == "dedicated-internal-key-value"
    assert s.internal_key != s.session_secret


def test_recovery_code_consumption_constant_time_helper() -> None:
    from daedalus.auth.totp import consume_recovery_code, hash_recovery_code

    code = "abcd-ef01-2345"
    stored = [hash_recovery_code(code), hash_recovery_code("ffff-eeee-dddd")]
    ok, remaining = consume_recovery_code(stored, code)
    assert ok is True
    assert len(remaining) == 1
    # A non-matching code leaves the list intact.
    ok2, remaining2 = consume_recovery_code(remaining, "0000-1111-2222")
    assert ok2 is False
    assert remaining2 == remaining
