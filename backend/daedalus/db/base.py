"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from daedalus.core.settings import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _utcnow() -> datetime:
    # tz-aware, app-side. Used by TimestampMixin.updated_at — see note there.
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NB: `onupdate` is *Python-side* deliberately. Using `func.now()` here
    # makes the UPDATE statement say `updated_at = now()`, which means the
    # post-UPDATE in-memory value is unknown to SQLAlchemy — it expires the
    # attribute, and the next access (e.g. `obj.updated_at.isoformat()` in
    # a response serializer) tries to lazy-load it. Under an async session
    # that lazy-load happens from sync attribute-access code and fails with
    # `MissingGreenlet: greenlet_spawn has not been called`. Computing the
    # value Python-side keeps the in-memory state consistent with the DB
    # write and avoids the expire-then-lazy-load trap entirely.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
