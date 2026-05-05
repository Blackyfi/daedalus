"""WebAuthn registration + authentication ceremonies.

Replaces or augments the TOTP step. Registration requires an authenticated
session (i.e. you've already enrolled TOTP, you're now adding a hardware
key). Authentication can substitute for the TOTP step at login time.

Browser → POST /auth/webauthn/register/begin → server returns publicKey opts
Browser → navigator.credentials.create(opts) → POST .../register/finish

Browser → POST /auth/webauthn/authenticate/begin (after password+OTP) →
Browser → navigator.credentials.get(opts) → POST .../authenticate/finish
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlparse

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.options_to_json import options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from daedalus.core.settings import get_settings
from daedalus.db.models import User, WebAuthnCredential

log = structlog.get_logger()


def _rp_id_origin() -> tuple[str, str]:
    public = get_settings().public_url
    parsed = urlparse(public)
    return parsed.hostname or "localhost", f"{parsed.scheme}://{parsed.netloc}"


def _b64url_dec(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _b64url_enc(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


async def begin_registration(db: AsyncSession, user: User) -> tuple[dict, str]:
    """Returns (options_json_dict, challenge_b64) — caller stashes the challenge in
    the session/redis to verify against."""
    rp_id, _ = _rp_id_origin()

    existing_res = await db.execute(
        select(WebAuthnCredential.credential_id).where(WebAuthnCredential.user_id == user.id)
    )
    excluded = [
        PublicKeyCredentialDescriptor(id=row[0]) for row in existing_res.all()
    ]

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Daedalus",
        user_id=str(user.id).encode(),
        user_name=user.email,
        user_display_name=user.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.PREFERRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.EDDSA,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
        exclude_credentials=excluded,
        timeout=60_000,
    )

    return json.loads(options_to_json(options)), _b64url_enc(options.challenge)


async def finish_registration(
    db: AsyncSession,
    user: User,
    expected_challenge_b64: str,
    response_payload: dict,
    nickname: str | None = None,
) -> WebAuthnCredential:
    rp_id, origin = _rp_id_origin()
    verification = verify_registration_response(
        credential=response_payload,
        expected_challenge=_b64url_dec(expected_challenge_b64),
        expected_rp_id=rp_id,
        expected_origin=origin,
        require_user_verification=False,
    )

    transports = response_payload.get("response", {}).get("transports") or []
    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=",".join(transports) if transports else None,
        nickname=nickname or "Hardware key",
    )
    db.add(cred)
    await db.flush()
    log.info("webauthn.registered", user_id=str(user.id), nickname=cred.nickname)
    return cred


async def begin_authentication(db: AsyncSession, user: User) -> tuple[dict, str]:
    rp_id, _ = _rp_id_origin()
    creds_res = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    creds = creds_res.scalars().all()
    allow_list = [
        PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds
    ]
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_list,
        user_verification=UserVerificationRequirement.PREFERRED,
        timeout=60_000,
    )
    return json.loads(options_to_json(options)), _b64url_enc(options.challenge)


async def finish_authentication(
    db: AsyncSession,
    user: User,
    expected_challenge_b64: str,
    response_payload: dict,
) -> WebAuthnCredential:
    rp_id, origin = _rp_id_origin()
    raw_id = _b64url_dec(response_payload["rawId"])
    cred_res = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == raw_id)
    )
    cred = cred_res.scalar_one_or_none()
    if cred is None or cred.user_id != user.id:
        raise ValueError("unknown credential for user")

    verification = verify_authentication_response(
        credential=response_payload,
        expected_challenge=_b64url_dec(expected_challenge_b64),
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=cred.public_key,
        credential_current_sign_count=cred.sign_count,
        require_user_verification=False,
    )
    cred.sign_count = verification.new_sign_count
    from datetime import datetime, timezone

    cred.last_used_at = datetime.now(timezone.utc)
    await db.flush()
    log.info("webauthn.authenticated", user_id=str(user.id), credential_id=cred.id)
    return cred


async def list_credentials(db: AsyncSession, user: User) -> list[WebAuthnCredential]:
    res = await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    return list(res.scalars().all())


async def delete_credential(db: AsyncSession, user: User, credential_pk: str) -> bool:
    import uuid as _uuid

    try:
        cid = _uuid.UUID(credential_pk)
    except ValueError:
        return False
    res = await db.execute(
        select(WebAuthnCredential).where(
            WebAuthnCredential.id == cid, WebAuthnCredential.user_id == user.id
        )
    )
    cred = res.scalar_one_or_none()
    if cred is None:
        return False
    await db.delete(cred)
    await db.flush()
    return True
