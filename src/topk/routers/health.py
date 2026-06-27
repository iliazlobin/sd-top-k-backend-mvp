"""GET /healthz — health check endpoint.

Checks PostgreSQL (SELECT 1) and Redis (PING) connectivity.
Returns 200 healthy even if Redis is down (degraded).
Returns 503 only if PostgreSQL is down.
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from topk.redis import check_redis_health

router = APIRouter(tags=["health"])

_start_time = time.monotonic()


@router.get("/healthz")
async def healthz(request: Request):
    """Return service health including DB and Redis connectivity."""
    postgres_status = "connected"
    redis_status = "disconnected"

    # Check PostgreSQL
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            postgres_status = "disconnected"
    else:
        postgres_status = "disconnected"

    # Check Redis
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is not None:
        try:
            if await check_redis_health(redis_client):
                redis_status = "connected"
        except Exception:
            pass

    uptime = time.monotonic() - _start_time

    response = {
        "status": "healthy",
        "redis": redis_status,
        "postgres": postgres_status,
        "window": "1h",
        "uptime_seconds": round(uptime, 3),
    }

    status_code = 200 if postgres_status == "connected" else 503
    return JSONResponse(content=response, status_code=status_code)
