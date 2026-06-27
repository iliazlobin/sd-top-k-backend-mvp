"""POST/GET/DELETE /admin/blacklist — blacklist CRUD.

Writes to PostgreSQL (source of truth) and updates the in-memory blacklist set.
Idempotent on re-adds and deletes of missing items.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from topk.db import get_db
from topk.models.event import Blacklist
from topk.models.schemas import (
    BlacklistAddRequest,
    BlacklistAddResponse,
    BlacklistListResponse,
    BlacklistRemoveRequest,
    BlacklistRemoveResponse,
)
from topk.services.trending import TrendingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


async def get_trending_service(request: Request) -> TrendingService:
    """FastAPI dependency: return the TrendingService from app state."""
    svc: TrendingService | None = getattr(
        request.app.state, "trending_service", None
    )
    if svc is None:
        raise RuntimeError("TrendingService not initialized")
    return svc


@router.post("/blacklist", response_model=BlacklistAddResponse, status_code=201)
async def add_blacklist(
    body: BlacklistAddRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Add items to the blacklist.

    Writes to PostgreSQL + in-memory set. Idempotent on re-adds.
    """
    added = 0
    added_ids: list[str] = []

    for item_id in body.item_ids:
        # Skip if already in memory (fast path)
        if item_id in trending.blacklist_set:
            continue

        # Check PostgreSQL
        stmt = select(Blacklist).where(Blacklist.item_id == item_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            db.add(Blacklist(item_id=item_id))
            added += 1

        # Always add to in-memory set (idempotent)
        trending.blacklist_set.add(item_id)
        added_ids.append(item_id)

    if added > 0:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("Failed to persist blacklist additions", exc_info=True)
            # Memory set was updated; PG write failed. In production this
            # would need reconciliation, but for MVP we log and continue.

    return BlacklistAddResponse(added=added, item_ids=added_ids)


@router.get("/blacklist", response_model=BlacklistListResponse)
async def list_blacklist(
    request: Request,
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """List all blacklisted item ids from in-memory set."""
    item_ids = sorted(trending.blacklist_set)
    return BlacklistListResponse(item_ids=item_ids, count=len(item_ids))


@router.delete("/blacklist", response_model=BlacklistRemoveResponse)
async def remove_blacklist(
    body: BlacklistRemoveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Remove items from the blacklist.

    Deletes from PostgreSQL + in-memory set. Idempotent on missing items.
    """
    removed = 0
    removed_ids: list[str] = []

    for item_id in body.item_ids:
        # Remove from in-memory set (idempotent)
        was_present = item_id in trending.blacklist_set
        trending.blacklist_set.discard(item_id)

        # Remove from PostgreSQL
        stmt = delete(Blacklist).where(Blacklist.item_id == item_id)
        result = await db.execute(stmt)
        pg_rows = result.rowcount if hasattr(result, 'rowcount') else 0

        if was_present or pg_rows > 0:
            removed += 1
        removed_ids.append(item_id)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.error("Failed to persist blacklist removals", exc_info=True)

    return BlacklistRemoveResponse(removed=removed, item_ids=removed_ids)


@router.post("/reset", status_code=200)
async def reset_state(
    request: Request,
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Reset all in-memory state and clear Redis caches.

    Used for test isolation. Recreates CMS, Space-Saving, Bloom filter,
    clears the blacklist set, and deletes all top-k Redis keys.
    """
    trending.reset()
    await trending.reset_redis()
    return {"status": "reset"}
