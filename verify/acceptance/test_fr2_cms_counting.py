"""FR-2: Count-Min Sketch counting.

After feeding N events for item X, GET /count returns count ≥ N (CMS overcounts).
Unknown item returns count = 0.
"""

from verify.acceptance.conftest import assert_202, assert_json_200, assert_422


def test_cms_count_known_item(client):
    """Feed 100 events for item 'x'; count must be ≥ 100."""
    # Ingest 100 events for item "x"
    events = [
        {"item_id": "x", "event_type": "view", "timestamp": 1719876543000 + i} for i in range(100)
    ]
    assert_202(client.post("/events", json={"events": events}))

    resp = assert_json_200(client.get("/count", params={"item_id": "x", "window": "1h"}))
    assert resp["item_id"] == "x"
    assert resp["window"] == "1h"
    assert resp["is_approximate"] is True
    assert resp["count"] >= 100, f"CMS overcounts: expected ≥100, got {resp['count']}"


def test_cms_count_unknown_item(client):
    """Unknown item 'never_seen' returns count = 0."""
    resp = assert_json_200(client.get("/count", params={"item_id": "never_seen", "window": "1h"}))
    assert resp["item_id"] == "never_seen"
    assert resp["count"] == 0
    assert resp["is_approximate"] is True


def test_cms_count_grows_with_more_events(client):
    """Feeding more events increases the count (monotonic)."""
    # First batch
    assert_202(
        client.post(
            "/events",
            json={
                "events": [
                    {"item_id": "monotonic", "event_type": "click", "timestamp": 1719876543000 + i}
                    for i in range(50)
                ]
            },
        )
    )
    count1 = assert_json_200(client.get("/count", params={"item_id": "monotonic", "window": "1h"}))[
        "count"
    ]

    # Second batch
    assert_202(
        client.post(
            "/events",
            json={
                "events": [
                    {"item_id": "monotonic", "event_type": "click", "timestamp": 1719876543000 + i}
                    for i in range(50, 100)
                ]
            },
        )
    )
    count2 = assert_json_200(client.get("/count", params={"item_id": "monotonic", "window": "1h"}))[
        "count"
    ]

    assert count2 >= count1, f"Count should be monotonic: {count1} → {count2}"


def test_cms_missing_item_id_422(client):
    """GET /count without item_id → 422."""
    assert_422(client.get("/count", params={"window": "1h"}))


def test_cms_invalid_window_422(client):
    """GET /count with invalid window → 422."""
    assert_422(client.get("/count", params={"item_id": "x", "window": "invalid"}))
