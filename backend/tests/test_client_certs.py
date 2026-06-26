"""Tests for the mTLS client-cert minting helper.

We generate a throwaway CA in a tempdir, mint a client cert against it,
and check the resulting artifacts: the cert verifies against the CA,
the fingerprint matches what we'd expect to be forwarded by Caddy, and
the .p12 bundle is decryptable with the password we supplied.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from daedalus.auth.client_certs import (
    MintedCert,
    fingerprint_of,
    mint_client_cert,
)


def _generate_throwaway_ca(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Daedalus Test CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "daedalus-test"),
        ]
    )
    now = _dt.datetime.now(tz=_dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = directory / "ca.crt"
    key_path = directory / "ca.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def ca_paths(tmp_path: Path) -> tuple[Path, Path]:
    ca_dir = tmp_path / "ca"
    ca_dir.mkdir()
    return _generate_throwaway_ca(ca_dir)


def test_mint_creates_all_three_artifacts(tmp_path: Path, ca_paths: tuple[Path, Path]) -> None:
    out_dir = tmp_path / "out"
    minted = mint_client_cert(
        email="alice@example.com",
        display_name="Alice",
        out_dir=out_dir,
        ca_cert_path=ca_paths[0],
        ca_key_path=ca_paths[1],
        p12_password="hunter2-correct-horse",
    )
    assert minted.key_path.exists()
    assert minted.cert_path.exists()
    assert minted.pkcs12_path.exists()
    assert isinstance(minted, MintedCert)


def test_minted_cert_chains_to_ca(tmp_path: Path, ca_paths: tuple[Path, Path]) -> None:
    minted = mint_client_cert(
        email="bob@example.com",
        display_name="Bob",
        out_dir=tmp_path,
        ca_cert_path=ca_paths[0],
        ca_key_path=ca_paths[1],
        p12_password="ignored",
    )

    cert = x509.load_pem_x509_certificate(minted.cert_path.read_bytes())
    ca_cert = x509.load_pem_x509_certificate(ca_paths[0].read_bytes())

    # Cert is signed by the CA's private key — verifying with the CA's
    # public key is the only check that matters for chaining here.
    ca_cert.public_key().verify(
        cert.signature,
        cert.tbs_certificate_bytes,
        padding=__import__(
            "cryptography.hazmat.primitives.asymmetric.padding",
            fromlist=["PKCS1v15"],
        ).PKCS1v15(),
        algorithm=cert.signature_hash_algorithm,
    )

    # Subject contains the email; SAN has it as RFC822Name.
    assert any(
        attr.value == "bob@example.com"
        for attr in cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)
    )
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "bob@example.com" in san.get_values_for_type(x509.RFC822Name)

    # ExtendedKeyUsage must include client-auth so Caddy will accept it.
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.ExtendedKeyUsageOID.CLIENT_AUTH in eku


def test_fingerprint_matches_caddy_format(tmp_path: Path, ca_paths: tuple[Path, Path]) -> None:
    minted = mint_client_cert(
        email="carol@example.com",
        display_name="Carol",
        out_dir=tmp_path,
        ca_cert_path=ca_paths[0],
        ca_key_path=ca_paths[1],
        p12_password="ignored",
    )
    cert = x509.load_pem_x509_certificate(minted.cert_path.read_bytes())
    assert minted.fingerprint_sha256 == fingerprint_of(cert)
    # 32 bytes → 32 colon-separated hex pairs
    assert minted.fingerprint_sha256.count(":") == 31
    assert minted.fingerprint_sha256 == minted.fingerprint_sha256.lower()


def test_pkcs12_bundle_round_trips_with_password(tmp_path: Path, ca_paths: tuple[Path, Path]) -> None:
    password = "S3cret-Bundle-Pwd!"
    minted = mint_client_cert(
        email="dave@example.com",
        display_name="Dave",
        out_dir=tmp_path,
        ca_cert_path=ca_paths[0],
        ca_key_path=ca_paths[1],
        p12_password=password,
    )
    bundle = minted.pkcs12_path.read_bytes()

    # Wrong password should fail.
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(bundle, b"wrong-password")

    key, cert, additional = pkcs12.load_key_and_certificates(bundle, password.encode())
    assert key is not None
    assert cert is not None
    # CA chain is bundled too
    assert any(c is not None for c in additional)


def test_missing_ca_files_raise(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        mint_client_cert(
            email="eve@example.com",
            display_name="Eve",
            out_dir=tmp_path,
            ca_cert_path=tmp_path / "nope.crt",
            ca_key_path=tmp_path / "nope.key",
            p12_password="ignored",
        )


def test_safe_filename_for_email(tmp_path: Path, ca_paths: tuple[Path, Path]) -> None:
    minted = mint_client_cert(
        email="frank@example.com",
        display_name="Frank",
        out_dir=tmp_path,
        ca_cert_path=ca_paths[0],
        ca_key_path=ca_paths[1],
        p12_password="ignored",
    )
    assert "frank_at_example.com" in minted.cert_path.name
    assert "@" not in minted.cert_path.name


def test_cli_help_lists_mint_client_cert() -> None:
    from click.testing import CliRunner

    from daedalus.cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "mint-client-cert" in result.output


def test_cli_mint_help_documents_options() -> None:
    from click.testing import CliRunner

    from daedalus.cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["mint-client-cert", "--help"])
    assert result.exit_code == 0
    for needle in ["--email", "--ca-cert", "--ca-key", "--out-dir", "--p12-password", "--pin"]:
        assert needle in result.output
