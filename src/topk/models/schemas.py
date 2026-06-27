"""Pydantic request/response schemas for the Top-K API."""

from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    view = "view"
    click = "click"
    mention = "mention"


class WindowSize(StrEnum):
    one_hour = "1h"


# ── Event ingestion ──────────────────────────────────────────────────────────


class EventIn(BaseModel):
    """A single event to be ingested."""

    item_id: str = Field(..., min_length=1)
    event_type: EventType
    timestamp: int = Field(..., gt=0)
    event_id: str | None = None
    user_id: str | None = None


class EventsRequest(BaseModel):
    """Batch of events to ingest."""

    events: list[EventIn] = Field(..., min_length=1, max_length=100)


class EventsResponse(BaseModel):
    """Response after processing a batch of events."""

    accepted: int
    duplicates: int = 0
    blocked: int = 0


# ── Trending ─────────────────────────────────────────────────────────────────


class TrendingItem(BaseModel):
    item_id: str
    count: int
    rank: int


class TrendingResponse(BaseModel):
    items: list[TrendingItem]
    window: str
    k: int
    updated_at: int


class CountResponse(BaseModel):
    item_id: str
    window: str
    count: int
    is_approximate: bool = True


# ── Admin / Blacklist ────────────────────────────────────────────────────────


class BlacklistAddRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1)


class BlacklistAddResponse(BaseModel):
    added: int
    item_ids: list[str]


class BlacklistRemoveRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1)


class BlacklistRemoveResponse(BaseModel):
    removed: int
    item_ids: list[str]


class BlacklistListResponse(BaseModel):
    item_ids: list[str]
    count: int


# ── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    redis: str
    postgres: str
    window: str
    uptime_seconds: float
