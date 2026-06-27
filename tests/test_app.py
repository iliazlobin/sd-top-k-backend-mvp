"""Skeleton tests: verify app starts and health endpoint responds."""

import pytest


class TestAppStartup:
    """Verify the FastAPI application can be created and serves requests."""

    def test_app_creates_without_error(self, app):
        """Application factory returns a FastAPI instance."""
        from fastapi import FastAPI

        assert isinstance(app, FastAPI)
        assert app.title == "Top-K Trending API"

    def test_openapi_schema_generates(self, app):
        """OpenAPI schema can be generated without errors."""
        schema = app.openapi()
        assert "openapi" in schema
        assert "/healthz" in schema["paths"]


class TestHealthEndpoint:
    """Verify GET /healthz returns the expected response structure."""

    @pytest.mark.asyncio
    async def test_healthz_returns_response(self, async_client):
        """GET /healthz returns a JSON response with required fields."""
        resp = await async_client.get("/healthz")
        # Health endpoint should respond (200 if DB is up, 503 if down)
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        assert "redis" in data
        assert "postgres" in data
        assert "window" in data
        assert data["window"] == "1h"
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] > 0

    @pytest.mark.asyncio
    async def test_healthz_postgres_connected(self, async_client):
        """When PostgreSQL is available, postgres status is 'connected'."""
        resp = await async_client.get("/healthz")
        if resp.status_code == 200:
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["postgres"] == "connected"
        else:
            data = resp.json()
            assert data["postgres"] == "disconnected"


class TestRouterValidation:
    """Verify endpoints return 422 for invalid input (validation runs before DB)."""

    @pytest.mark.asyncio
    async def test_post_events_validation_returns_422(self, async_client):
        """POST /events with empty body should return 422."""
        resp = await async_client.post("/events", json={"events": []})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_trending_validation_returns_422(self, async_client):
        """GET /trending without params should return 422."""
        resp = await async_client.get("/trending")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_count_validation_returns_422(self, async_client):
        """GET /count without item_id should return 422."""
        resp = await async_client.get("/count")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_blacklist_empty_validation_returns_422(self, async_client):
        """POST /admin/blacklist with empty item_ids should return 422."""
        resp = await async_client.post("/admin/blacklist", json={"item_ids": []})
        assert resp.status_code == 422
