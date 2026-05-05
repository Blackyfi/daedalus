"""Async Redis client. Single shared connection pool per process."""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from daedalus.core.settings import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(get_settings().redis_url, encoding="utf-8", decode_responses=False)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
