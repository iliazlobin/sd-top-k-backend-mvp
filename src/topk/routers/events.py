"""POST /events — batch event ingestion with dedup, counting, and tracking.

Pydantic validation → idempotency check → pipeline (Bloom dedup →
blacklist check → CMS increment → Space-Saving update) → Redis flush →
PostgreSQL persistence → 202 response.
"""

from __future__ import annotations

import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from topk.db import get_db
from topk.models.event import Event
from topk.models.schemas import EventType, EventsRequest, EventsResponse
from topk.services.trending import TrendingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


async def get_trending_service(request: Request) -> TrendingService:
    """FastAPI dependency: return the TrendingService from app state."""
    svc: TrendingService | None = getattr(request.app.state, "trending_service", None)
    if svc is None:
        raise RuntimeError("TrendingService not initialized")
    return svc


@router.post("", response_model=EventsResponse, status_code=202)
async def ingest_events(
    body: EventsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Ingest a batch of up to 100 events.

    Pipeline: validate → idempotency check → Bloom dedup →
    blacklist check → CMS increment → Space-Saving update →
    flush to Redis → persist to PostgreSQL.
    """
    events_in = body.events

    # 1. Assign event_ids and do intra-batch dedup first (cheapest)
    accepted = 0
    duplicates = 0
    blocked = 0
    events_to_persist: list[Event] = []

    seen_in_batch: set[str] = set()
    valid_provided_ids: set[str] = set()  # Valid UUIDs for PG check
    event_map: dict[str, tuple[uuid.UUID, str, str, int, str | None]] = {}
    # Maps event_id_str → (uuid, item_id, event_type, timestamp, user_id)

    for ev in events_in:
        if ev.event_id is not None:
            eid_str = ev.event_id
        else:
            eid_str = str(uuid.uuid4())

        # Intra-batch duplicate?
        if eid_str in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(eid_str)

        # Validate UUID format
        try:
            eid = uuid.UUID(eid_str)
        except ValueError:
            # Invalid UUID — skip (Pydantic doesn't validate UUID format)
            continue

        valid_provided_ids.add(eid_str)
        event_map[eid_str] = (
            eid, ev.item_id, ev.event_type.value,
            ev.timestamp, ev.user_id,
        )

    # 2. Batch-check PostgreSQL for existing event_ids (idempotency)
    existing_ids: set[str] = set()
    if valid_provided_ids:
        try:
            uuids = [uuid.UUID(eid) for eid in valid_provided_ids]
            stmt = select(Event.event_id).where(Event.event_id.in_(uuids))
            result = await db.execute(stmt)
            existing_ids = {str(row[0]) for row in result.fetchall()}
        except Exception:
            logger.warning("DB idempotency check failed", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={"detail": "Database unavailable"},
            )

    # 3. Process each unique, non-duplicate event
    for eid_str, (eid, item_id, event_type, timestamp, user_id) in event_map.items():
        # Cross-request duplicate? (already in PostgreSQL)
        if eid_str in existing_ids:
            duplicates += 1
            continue

        # Process through the trending pipeline
        result = trending.process_event(
            item_id=item_id,
            event_type=event_type,
            timestamp_ms=timestamp,
            user_id=user_id,
        )

        if result["deduped"]:
            # Bloom filter dedup — silently drop (not counted in duplicates)
            continue

        if result["blocked"]:
            blocked += 1

        accepted += 1

        # Prepare for PostgreSQL persistence
        events_to_persist.append(
            Event(
                event_id=eid,
                item_id=item_id,
                user_id=user_id,
                event_type=event_type,
                timestamp=timestamp,
            )
        )

    # 4. Flush trending to Redis (if any events were processed)
    if accepted > 0:
        try:
            await trending.flush_to_redis()
        except Exception:
            logger.warning("Redis flush failed during event ingestion", exc_info=True)

    # 5. Persist to PostgreSQL
    if events_to_persist:
        try:
            db.add_all(events_to_persist)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("Failed to persist events to PostgreSQL", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={"detail": "Database write failed"},
            )

    return EventsResponse(
        accepted=accepted,
        duplicates=duplicates,
        blocked=blocked,
    )
