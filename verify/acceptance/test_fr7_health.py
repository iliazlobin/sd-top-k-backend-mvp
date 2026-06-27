"""FR-7: Health endpoint.

GET /healthz → 200 with status, redis, postgres, window, uptime_seconds.
"""

from verify.acceptance.conftest import assert_json_200


def test_healthz_returns_healthy(client):
    """GET /healthz → 200 with all expected fields and healthy status."""
    resp = assert_json_200(client.get("/healthz"))

    assert resp["status"] == "healthy"
    assert "redis" in resp
    assert "postgres" in resp
    assert resp["window"] == "1h"
    assert "uptime_seconds" in resp
    assert resp["uptime_seconds"] > 0


def test_healthz_connections_are_connected(client):
    """Redis and postgres should report 'connected'."""
    resp = assert_json_200(client.get("/healthz"))
    assert resp["postgres"] == "connected", \
        f"Postgres should be connected, got {resp['postgres']}"
    assert resp["redis"] == "connected", \
        f"Redis should be connected, got {resp['redis']}"


def test_healthz_no_auth_required(client):
    """GET /healthz accessible without any auth headers."""
    # Remove any headers and test
    r = client.get("/healthz")
    assert r.status_code == 200, f"Healthz should be public, got {r.status_code}"
