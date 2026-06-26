"""Ed25519 connector-pack signature verification (IMPROVEMENTS #14)."""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from daedalus.connectors.signing import load_pubkey, verify_signature


def _keypair():
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, load_pubkey(pem)


def test_valid_signature_verifies():
    priv, pub = _keypair()
    spec = b'{"id":"demo"}'
    sig = base64.b64encode(priv.sign(spec)).decode()
    assert verify_signature(spec, sig, pub) is True


def test_tampered_spec_fails():
    priv, pub = _keypair()
    sig = base64.b64encode(priv.sign(b'{"id":"demo"}')).decode()
    assert verify_signature(b'{"id":"evil"}', sig, pub) is False


def test_garbage_signature_fails():
    _priv, pub = _keypair()
    assert verify_signature(b"x", "not-base64!!", pub) is False
    assert verify_signature(b"x", base64.b64encode(b"short").decode(), pub) is False


def test_load_pubkey_rejects_non_ed25519():
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    with pytest.raises(ValueError):
        load_pubkey(rsa_pem)
