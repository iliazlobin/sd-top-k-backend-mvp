"""Redis client for Top-K MVP — async redis-py interface."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.asyncio import Redis

from topk.config import settings

logger = logging.getLogger(__name__)


def create_redis_client(redis_url: str | None = None) -> Redis:
    """Create a new async Redis client from a URL."""
    url = redis_url or settings.REDIS_URL
    return aioredis.from_url(url, decode_responses=True)


# Module-level singleton — created lazily to avoid import-time failures
_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    """Return the module-level Redis client, creating it if needed."""
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client()
    return _redis_client


async def check_redis_health(client: Redis | None = None) -> bool:
    """Ping Redis. Returns True if connected."""
    c = client or get_redis_client()
    try:
        await c.ping()
        return True
    except Exception:
        return False
