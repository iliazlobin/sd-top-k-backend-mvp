"""FR-4: Top-K retrieval.

GET /trending?window=1h&k={10..1000} → 200 with items array.
Invalid k → 422. Invalid window → 422.
Redis stale/missing → 503 (graceful degradation).
"""

from verify.acceptance.conftest import assert_202, assert_422, assert_json_200


def test_trending_valid_k_returns_200(client):
    """GET /trending with valid k → 200 with items."""
    # Seed some events so the cache is populated
    events = [
        {"item_id": f"k_test_{i}", "event_type": "view", "timestamp": 1719876543000 + i}
        for i in range(15)
    ]
    assert_202(client.post("/events", json={"events": events}))

    resp = assert_json_200(client.get("/trending", params={"window": "1h", "k": 10}))
    assert isinstance(resp["items"], list)
    assert resp["k"] == 10
    assert resp["window"] == "1h"


def test_trending_k_zero_422(client):
    """k=0 → 422."""
    assert_422(client.get("/trending", params={"window": "1h", "k": 0}))


def test_trending_k_negative_422(client):
    """k=-1 → 422."""
    assert_422(client.get("/trending", params={"window": "1h", "k": -1}))


def test_trending_k_too_large_422(client):
    """k=1001 (> 1000 max) → 422."""
    assert_422(client.get("/trending", params={"window": "1h", "k": 1001}))


def test_trending_invalid_window_422(client):
    """Invalid window value → 422."""
    assert_422(client.get("/trending", params={"window": "invalid", "k": 10}))
    assert_422(client.get("/trending", params={"window": "24h", "k": 10}))
    assert_422(client.get("/trending", params={"window": "1m", "k": 10}))


def test_trending_missing_window_422(client):
    """Missing window param → 422."""
    assert_422(client.get("/trending", params={"k": 10}))


def test_trending_missing_k_default_or_422(client):
    """Missing k → either 422 or default behavior. Test for graceful handling."""
    r = client.get("/trending", params={"window": "1h"})
    # Either 422 (strict) or 200 with default k
    assert r.status_code in (200, 422), f"Expected 200 or 422, got {r.status_code}: {r.text}"
