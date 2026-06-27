"""FastAPI application factory with lifespan management.

Initializes database engine, Redis client, TrendingService (CMS,
Space-Saving, Bloom, SlidingWindow, Blacklist), and starts the
background trending refresh loop.
"""

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import text

from topk.config import settings
from topk.db import create_engine
from topk.redis import create_redis_client
from topk.routers import admin, events, health, trending
from topk.services.trending import TrendingService

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Run Alembic migrations to ensure the database schema is up to date.

    Searches for alembic.ini in the current working directory and the project
    root (one level up from src/topk).  Runs ``alembic upgrade head`` in a
    subprocess so the migration engine gets its own clean event loop (alembic's
    async env.py uses ``asyncio.run()`` which cannot nest inside uvicorn's).
    Migrations are idempotent — Alembic tracks applied revisions.
    """
    # Find alembic.ini: try cwd, then project root (src/topk's grandparent)
    candidates = [
        Path("alembic.ini"),
        Path(__file__).resolve().parent.parent.parent / "alembic.ini",
    ]
    alembic_ini: Path | None = None
    project_root: Path | None = None
    for c in candidates:
        if c.is_file():
            alembic_ini = c
            project_root = c.parent
            break

    if alembic_ini is None:
        logger.warning(
            "alembic.ini not found — skipping migrations; the database schema must already exist"
        )
        return

    try:
        env = os.environ.copy()
        # Pass DATABASE_URL to the subprocess so alembic env.py picks it up
        env["DATABASE_URL"] = settings.DATABASE_URL
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(project_root),
            env=env,
        )
        if result.returncode == 0:
            logger.info("Database migrations applied successfully")
            if result.stdout.strip():
                logger.debug("alembic stdout: %s", result.stdout.strip())
        else:
            logger.warning(
                "alembic upgrade exited %d — stderr: %s",
                result.returncode,
                result.stderr.strip()[-300:],
            )
    except FileNotFoundError:
        logger.warning(
            "alembic CLI not found — skipping migrations; "
            "ensure the schema exists or run 'alembic upgrade head' manually"
        )
    except Exception:
        logger.warning(
            "Database migration failed — continuing with existing schema",
            exc_info=True,
        )


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application.

    Each call creates a fresh engine, Redis client, and TrendingService,
    so tests can create/destroy apps without interfering.
    """
    # Run database migrations synchronously (BEFORE any async engine or lifespan).
    # alembic env.py uses asyncio.run() internally which cannot be called from
    # inside an already-running event loop (uvicorn's).  Running here ensures
    # a clean sync context for the migration engine.
    _run_migrations()

    engine = create_engine()
    redis_client = create_redis_client()

    trending_service = TrendingService(
        epsilon=settings.CMS_EPSILON,
        delta=settings.CMS_DELTA,
        space_saving_capacity=settings.SPACE_SAVING_CAPACITY,
        bloom_capacity=settings.BLOOM_CAPACITY,
        bloom_error_rate=settings.BLOOM_ERROR_RATE,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        logger.info("Starting Top-K MVP...")

        # Connect Redis
        try:
            await redis_client.ping()
            logger.info("Redis connected")
        except Exception:
            logger.warning("Redis unavailable — starting in degraded mode")

        # Load blacklist from PostgreSQL
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT item_id FROM blacklist"))
                for row in result.fetchall():
                    trending_service.blacklist_set.add(row[0])
            logger.info(
                "Loaded %d blacklist entries from PostgreSQL",
                len(trending_service.blacklist_set),
            )
        except Exception:
            logger.warning("Could not load blacklist from PostgreSQL — starting with empty set")

        # Wire Redis into TrendingService
        trending_service.redis = redis_client

        # Store on app state for routers
        app.state.engine = engine
        app.state.redis_client = redis_client
        app.state.trending_service = trending_service

        # Start background refresh task
        refresh_task = asyncio.create_task(_refresh_loop(trending_service))

        yield

        # Shutdown
        logger.info("Shutting down Top-K MVP...")
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

        await redis_client.aclose()
        await engine.dispose()

    app = FastAPI(
        title="Top-K Trending API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Set engine and services on app state for module-level access too
    app.state.engine = engine
    app.state.redis_client = redis_client
    app.state.trending_service = trending_service

    app.include_router(events.router)
    app.include_router(trending.router)
    app.include_router(admin.router)
    app.include_router(health.router)

    return app


async def _refresh_loop(trending_service: TrendingService) -> None:
    """Background task: flush trending to Redis every REFRESH_INTERVAL seconds."""
    interval = settings.REFRESH_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval)
        try:
            await trending_service.flush_to_redis()
        except Exception:
            logger.warning("Background refresh failed", exc_info=True)


# Module-level singleton for uvicorn (imported as `topk.main:app`)
# Created lazily to avoid import-time DB/Redis connection failures.
_app: FastAPI | None = None


def get_app() -> FastAPI:
    """Return the module-level FastAPI app, creating it lazily."""
    global _app
    if _app is None:
        _app = create_app()
    return _app


# Expose as `app` for uvicorn's module string
def __getattr__(name: str):
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
