"""GET /trending and GET /count — top-K retrieval and point queries.

GET /trending?window=1h&k={10..1000} → reads Redis sorted set
GET /count?item_id=X&window=1h → reads CMS directly
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from topk.models.schemas import CountResponse, TrendingResponse, WindowSize
from topk.services.trending import TrendingService

router = APIRouter(tags=["trending"])


async def get_trending_service(request: Request) -> TrendingService:
    """FastAPI dependency: return the TrendingService from app state."""
    svc: TrendingService | None = getattr(request.app.state, "trending_service", None)
    if svc is None:
        raise RuntimeError("TrendingService not initialized")
    return svc


@router.get("/trending", response_model=TrendingResponse)
async def get_trending(
    request: Request,
    window: str = Query(..., pattern=r"^1h$"),
    k: int = Query(..., ge=1, le=1000),
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Return the top-K trending items for the given window.

    Reads from Redis sorted set. Returns 503 if the cache is stale
    or unavailable (cold start / pipeline stall).
    """
    result = await trending.get_trending(k)
    if result is None:
        return JSONResponse(
            status_code=503,
            content={
                "stale_data": True,
                "detail": "Top-K cache not available",
            },
        )

    return TrendingResponse(**result)


@router.get("/count", response_model=CountResponse)
async def get_count(
    request: Request,
    item_id: str = Query(..., min_length=1),
    window: str = Query("1h", pattern=r"^1h$"),
    trending: TrendingService = Depends(get_trending_service),
) -> Any:
    """Return the CMS count estimate for a single item.

    Reads directly from the in-memory sliding window (60 CMS buckets).
    No Redis involved. Unknown items return count=0 (CMS guarantee).
    """
    result = trending.get_count(item_id)
    return CountResponse(**result)
