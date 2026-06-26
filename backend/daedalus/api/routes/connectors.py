"""Connector CRUD + JSON-Schema validation."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daedalus.api.schemas import ConnectorIn, ConnectorOut, ConnectorOverridesIn
from daedalus.auth.audit import record
from daedalus.auth.dependencies import current_user, require_role
from daedalus.connectors.loader import ConnectorImportError, import_connectors_from_dir
from daedalus.connectors.schema import CONNECTOR_SCHEMA
from daedalus.core.settings import get_settings
from daedalus.db.base import get_session
from daedalus.db.models import Connector, Role, User

router = APIRouter()


def _validate(spec: dict) -> None:
    errs = sorted(Draft202012Validator(CONNECTOR_SCHEMA).iter_errors(spec), key=lambda e: e.path)
    if errs:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"validation_errors": [{"path": list(e.path), "message": e.message} for e in errs]},
        )


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(
    user: Annotated[User, Depends(current_user)],
    include_disabled: bool = False,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Connector).order_by(Connector.connector_id.asc())
    if not include_disabled:
        stmt = stmt.where(Connector.enabled.is_(True))
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post("/{cid}/enable", response_model=ConnectorOut,
             dependencies=[Depends(require_role(Role.owner))])
async def enable_connector(
    cid: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    return await _set_enabled(db, request, user, cid, True)


@router.post("/{cid}/disable", response_model=ConnectorOut,
             dependencies=[Depends(require_role(Role.owner))])
async def disable_connector(
    cid: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    return await _set_enabled(db, request, user, cid, False)


async def _set_enabled(db: AsyncSession, request: Request, user: User, cid: str, enabled: bool):
    res = await db.execute(select(Connector).where(Connector.connector_id == cid))
    connector = res.scalar_one_or_none()
    if not connector:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    connector.enabled = enabled
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="connector.enable" if enabled else "connector.disable",
        target_kind="connector", target_id=cid,
    )
    await db.commit()
    return connector


@router.post("/reload", dependencies=[Depends(require_role(Role.owner))])
async def reload_connectors(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    """Re-import the on-disk connector pack (`settings.connectors_dir`) into the
    DB. Owner-only. New/edited specs go live without a restart; invalid specs
    abort the whole reload (400) leaving the DB untouched."""
    try:
        summary = await import_connectors_from_dir(db, get_settings().connectors_dir)
    except ConnectorImportError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="connector.reload", target_kind="connector", target_id="*",
        payload=summary,
    )
    await db.commit()
    return summary


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_role(Role.owner))])
async def upsert_connector(
    body: ConnectorIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    _validate(body.spec)
    cid = body.spec.get("id")
    if not cid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "spec.id is required")

    res = await db.execute(select(Connector).where(Connector.connector_id == cid))
    existing = res.scalar_one_or_none()
    if existing:
        existing.spec = body.spec
        existing.display_name = body.spec.get("display_name", cid)
        connector = existing
    else:
        connector = Connector(
            connector_id=cid,
            display_name=body.spec.get("display_name", cid),
            spec=body.spec,
        )
        db.add(connector)
    await db.flush()
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="connector.upsert", target_kind="connector", target_id=cid,
    )
    await db.commit()
    return connector


@router.patch("/{cid}/overrides", response_model=ConnectorOut,
              dependencies=[Depends(require_role(Role.owner))])
async def patch_connector_overrides(
    cid: str,
    body: ConnectorOverridesIn,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    """Update the connector's emergency project-override settings.

    PATCH semantics: only fields present in the request body are written.
    Pass a field as null to clear that specific override.
    """
    res = await db.execute(select(Connector).where(Connector.connector_id == cid))
    connector = res.scalar_one_or_none()
    if not connector:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(connector, field, value)

    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="connector.overrides_patch", target_kind="connector", target_id=cid,
    )
    await db.commit()
    await db.refresh(connector)
    return connector


@router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_role(Role.owner))])
async def delete_connector(
    cid: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: AsyncSession = Depends(get_session),
):
    res = await db.execute(select(Connector).where(Connector.connector_id == cid))
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(c)
    await record(
        db, actor_user_id=user.id, actor_cert_fp=request.state.cert_fp,
        action="connector.delete", target_kind="connector", target_id=cid,
    )
    await db.commit()
