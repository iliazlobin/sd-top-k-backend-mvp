"""Shared fixtures for Top-K white-box tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from topk.main import create_app


@pytest.fixture
def app():
    """Return a fresh FastAPI application instance."""
    return create_app()


@pytest.fixture
async def async_client(app):
    """Async HTTP client bound to the app via ASGI transport."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
