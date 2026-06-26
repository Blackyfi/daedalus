"""Optional connector-pack signature verification (IMPROVEMENTS #14).

Hot-reloading on-disk connector specs is effectively "load code that decides
what the agent runs" — a rogue write to the connectors dir is a latent RCE.
When ``CONNECTOR_SIGNING_REQUIRED=true`` and a public key is configured, the
loader verifies a detached Ed25519 signature (``<spec>.json.sig``, base64) for
every spec and **fails closed** (refuses the whole import) on any miss.

Default OFF — no signatures required — so existing deployments are unaffected
until they opt in by setting a key + flag and dropping ``.sig`` files.
"""
from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def load_pubkey(pem: str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from PEM. Raises ValueError if it isn't one."""
    key = load_pem_public_key(pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("connector signing key must be Ed25519")
    return key


def verify_signature(spec_bytes: bytes, signature_b64: str, pubkey: Ed25519PublicKey) -> bool:
    """True iff `signature_b64` (base64 Ed25519) signs `spec_bytes` under `pubkey`."""
    try:
        sig = base64.b64decode(signature_b64.strip(), validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    try:
        pubkey.verify(sig, spec_bytes)
        return True
    except InvalidSignature:
        return False
