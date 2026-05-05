"""Issue mTLS client certificates against the internal CA.

Used by the ``mint-client-cert`` CLI to mint browser-installable certs
for operators. The Caddy reverse proxy is already configured to require
that every connection present a cert signed by the same CA bundle, so
once the operator imports the resulting ``.p12`` into their browser
they can hit the platform.

Outputs (per invocation):

* ``<email>.key`` — PEM-encoded private key (4096-bit RSA, no passphrase).
* ``<email>.crt`` — PEM-encoded signed cert.
* ``<email>.p12`` — PKCS#12 bundle (key + cert), encrypted with the
  operator-supplied password. This is what gets imported into the
  browser.

Returns the SHA-256 fingerprint of the issued cert, in the same
``aa:bb:…`` lowercase form Caddy forwards via
``X-Client-Cert-Fingerprint``. Pin it to the target user via
``User.pinned_cert_fingerprint`` if the caller asks for it.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


_DEFAULT_KEY_BITS = 4096


@dataclass(frozen=True)
class MintedCert:
    """Result of a successful mint."""

    fingerprint_sha256: str  # lowercase, colon-separated hex (matches Caddy)
    key_path: Path
    cert_path: Path
    pkcs12_path: Path
    not_before: _dt.datetime
    not_after: _dt.datetime
    serial_number: int


def load_ca(ca_cert_path: Path, ca_key_path: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the CA cert + private key from disk.

    Raises ``FileNotFoundError`` for missing inputs and ``ValueError`` if
    the parsed key isn't an RSA key — we don't sign with EC keys here on
    purpose; the issuing-CA story for self-hosted org PKI is almost
    always RSA.
    """
    cert_bytes = Path(ca_cert_path).read_bytes()
    key_bytes = Path(ca_key_path).read_bytes()
    cert = x509.load_pem_x509_certificate(cert_bytes)
    key = serialization.load_pem_private_key(key_bytes, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("CA key must be RSA (self-hosted org-PKI assumption)")
    return cert, key


def mint_client_cert(
    *,
    email: str,
    display_name: str,
    out_dir: Path,
    ca_cert_path: Path,
    ca_key_path: Path,
    p12_password: str,
    days: int = 365,
    key_bits: int = _DEFAULT_KEY_BITS,
) -> MintedCert:
    """Generate a key, sign a client cert against the CA, write all three artifacts."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_cert, ca_key = load_ca(ca_cert_path, ca_key_path)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_bits)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, email),
            x509.NameAttribute(NameOID.EMAIL_ADDRESS, email),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "daedalus-clients"),
            x509.NameAttribute(NameOID.GIVEN_NAME, display_name or email),
        ]
    )

    not_before = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=5)
    not_after = not_before + _dt.timedelta(days=days)
    serial = x509.random_serial_number()

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(private_key.public_key())
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(email)]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
    )

    cert = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())

    # Write artifacts.
    safe_stem = email.replace("@", "_at_").replace("/", "_")
    key_path = out_dir / f"{safe_stem}.key"
    cert_path = out_dir / f"{safe_stem}.crt"
    p12_path = out_dir / f"{safe_stem}.p12"

    _write_secure(
        key_path,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    _write_secure(cert_path, cert.public_bytes(serialization.Encoding.PEM))

    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=email.encode("utf-8"),
        key=private_key,
        cert=cert,
        cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(p12_password.encode("utf-8")),
    )
    _write_secure(p12_path, p12_bytes)

    return MintedCert(
        fingerprint_sha256=fingerprint_of(cert),
        key_path=key_path,
        cert_path=cert_path,
        pkcs12_path=p12_path,
        not_before=not_before,
        not_after=not_after,
        serial_number=serial,
    )


def fingerprint_of(cert: x509.Certificate) -> str:
    """SHA-256 fingerprint formatted to match Caddy's forwarded header.

    Caddy emits ``aa:bb:cc:…`` lowercase. This is the exact format we
    persist into ``User.pinned_cert_fingerprint``.
    """
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02x}" for b in digest)


def _write_secure(path: Path, data: bytes) -> None:
    """Write a file with 0600 permissions to discourage casual leaks."""
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        # Best-effort; on some shared volumes chmod is restricted and the
        # write itself is the security boundary anyway.
        pass
