"""Database engine and session factory (SQLAlchemy async)."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from topk.config import settings


def create_engine(database_url: str | None = None) -> AsyncEngine:
    """Create a new async SQLAlchemy engine."""
    url = database_url or settings.DATABASE_URL
    return create_async_engine(url, echo=False, pool_size=5, max_overflow=10)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a new async session factory bound to the given engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Module-level engine and session factory — created lazily on first import
# to avoid crashing when DATABASE_URL env var uses a non-async driver.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(_get_engine())
    return _session_factory


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency: yield an async database session."""
    factory = _get_session_factory()
    async with factory() as session:
        yield session
