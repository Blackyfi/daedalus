"""Import the on-disk connector pack into the DB.

Connectors are read from the DB at runtime, so re-importing the pack from
``settings.connectors_dir`` makes new/edited specs live without a restart
(the "hot-reload" path). Shared by the CLI ``import-connectors`` command and
the ``POST /connectors/reload`` endpoint. The caller owns the transaction —
this never commits, so a validation error leaves the DB untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.connectors.schema import CONNECTOR_SCHEMA
from daedalus.connectors.signing import load_pubkey, verify_signature
from daedalus.core.settings import get_settings
from daedalus.db.models import Connector


class ConnectorImportError(ValueError):
    """A connector file was missing, malformed, or failed schema validation."""


async def import_connectors_from_dir(db: AsyncSession, directory: str | Path) -> dict:
    """Upsert every ``*.json`` connector spec under *directory* into the DB.

    Returns a summary dict. Raises ``ConnectorImportError`` (before any change
    can be committed) if the directory is empty or any spec is invalid.
    """
    directory = Path(directory)
    files = sorted(p for p in directory.glob("*.json") if p.is_file())
    if not files:
        raise ConnectorImportError(f"no connector JSON files found in {directory}")

    validator = Draft202012Validator(CONNECTOR_SCHEMA)

    # Optional, fail-closed signature gate (#14). Off unless required + key set.
    settings = get_settings()
    signing_pubkey = None
    if settings.connector_signing_required:
        if not settings.connector_signing_pubkey:
            raise ConnectorImportError(
                "CONNECTOR_SIGNING_REQUIRED is set but CONNECTOR_SIGNING_PUBKEY is missing"
            )
        try:
            signing_pubkey = load_pubkey(settings.connector_signing_pubkey)
        except ValueError as exc:
            raise ConnectorImportError(f"invalid connector signing key: {exc}") from exc

    added = 0
    updated = 0
    ids: list[str] = []

    for file_path in files:
        raw = file_path.read_bytes()
        if signing_pubkey is not None:
            sig_path = file_path.with_suffix(file_path.suffix + ".sig")
            if not sig_path.is_file():
                raise ConnectorImportError(f"{file_path.name}: missing signature {sig_path.name}")
            if not verify_signature(raw, sig_path.read_text(), signing_pubkey):
                raise ConnectorImportError(f"{file_path.name}: signature verification failed")
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectorImportError(f"{file_path.name}: invalid JSON: {exc}") from exc
        errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
        if errors:
            msg = "; ".join(
                f"{'/'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors
            )
            raise ConnectorImportError(f"{file_path.name}: {msg}")

        cid = spec["id"]
        existing = (
            await db.execute(select(Connector).where(Connector.connector_id == cid))
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Connector(
                    connector_id=cid,
                    display_name=spec.get("display_name", cid),
                    spec=spec,
                )
            )
            added += 1
        else:
            existing.display_name = spec.get("display_name", cid)
            existing.spec = spec
            updated += 1
        ids.append(cid)

    return {
        "imported": added + updated,
        "added": added,
        "updated": updated,
        "connector_ids": sorted(ids),
    }
