"""Application configuration via pydantic-settings.

Reads from environment variables with sensible defaults.
TOPK_DATABASE_URL takes precedence over DATABASE_URL to avoid
conflicts with other services running in the same environment.
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-K application settings, all configurable via environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://topk@localhost:5432/topk"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Server
    APP_PORT: int = 8000

    # Count-Min Sketch parameters
    CMS_EPSILON: float = 0.01
    CMS_DELTA: float = 0.001

    # Space-Saving parameters
    SPACE_SAVING_CAPACITY: int = 1000

    # Bloom filter parameters
    BLOOM_CAPACITY: int = 10_000_000
    BLOOM_ERROR_RATE: float = 0.001

    # Trending refresh interval (seconds)
    REFRESH_INTERVAL_SECONDS: int = 30


def _build_settings() -> Settings:
    """Build settings.

    Prefer TOPK_-prefixed env vars. For DATABASE_URL, ignore the
    environment variable unless TOPK_DATABASE_URL is explicitly set,
    to avoid conflicts with other services' DATABASE_URL.
    """
    # Determine DATABASE_URL
    database_url = Settings.model_fields["DATABASE_URL"].default
    if "TOPK_DATABASE_URL" in os.environ:
        database_url = os.environ["TOPK_DATABASE_URL"]
    elif "DATABASE_URL" in os.environ:
        env_url = os.environ["DATABASE_URL"]
        # Only use if it's an async URL; otherwise keep default
        if "+asyncpg" in env_url or "+aiosqlite" in env_url:
            database_url = env_url

    # Determine REDIS_URL (mirror DATABASE_URL: TOPK_ override, else env, else default)
    redis_url = Settings.model_fields["REDIS_URL"].default
    if "TOPK_REDIS_URL" in os.environ:
        redis_url = os.environ["TOPK_REDIS_URL"]
    elif "REDIS_URL" in os.environ:
        redis_url = os.environ["REDIS_URL"]

    app_port = Settings.model_fields["APP_PORT"].default
    if "TOPK_APP_PORT" in os.environ:
        app_port = int(os.environ["TOPK_APP_PORT"])

    # Build settings with explicit values (bypass env var reading)
    # We clear the env vars that might conflict, instantiate, then restore
    saved_db = os.environ.pop("DATABASE_URL", None)
    saved_redis = os.environ.pop("REDIS_URL", None)
    try:
        s = Settings(
            DATABASE_URL=database_url,
            REDIS_URL=redis_url,
            APP_PORT=app_port,
        )
    finally:
        if saved_db is not None:
            os.environ["DATABASE_URL"] = saved_db
        if saved_redis is not None:
            os.environ["REDIS_URL"] = saved_redis

    return s


settings = _build_settings()
