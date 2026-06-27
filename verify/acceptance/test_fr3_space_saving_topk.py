"""FR-3: Space-Saving top-K tracking.

After feeding skewed data, the heavy hitter ranks #1 with count ≥ its true frequency.
Result length ≤ k.
"""

from verify.acceptance.conftest import assert_202, assert_json_200


def test_space_saving_top_item_ranks_first(client):
    """Feed skewed data: 'hot'=1000, 9 others=100 each. 'hot' must be #1."""
    # Build all events, then post in batches of ≤100
    events = []

    # 1000 events for "hot"
    for i in range(1000):
        events.append({"item_id": "hot", "event_type": "view", "timestamp": 1719876543000 + i})

    # 100 events each for 9 other items
    for j in range(1, 10):
        item = f"warm_{j}"
        for i in range(100):
            events.append(
                {
                    "item_id": item,
                    "event_type": "view",
                    "timestamp": 1719876543000 + 1000 + j * 100 + i,
                }
            )

    # Chunk into batches of 100 to respect the batch cap
    for i in range(0, len(events), 100):
        assert_202(client.post("/events", json={"events": events[i : i + 100]}))

    resp = assert_json_200(client.get("/trending", params={"window": "1h", "k": 3}))
    items = resp["items"]
    assert len(items) > 0, "Expected at least one item in trending"
    assert len(items) <= 3, f"Expected ≤3 items, got {len(items)}"

    # "hot" should be #1 with count ≥ 1000
    assert items[0]["item_id"] == "hot", f"Expected 'hot' #1, got {items[0]['item_id']}"
    assert items[0]["count"] >= 1000, f"Expected 'hot' count ≥ 1000, got {items[0]['count']}"
    assert items[0]["rank"] == 1


def test_space_saving_result_count_le_k(client):
    """GET /trending?k=10 returns at most 10 items."""
    # Feed a few events to populate the system
    events = []
    for i in range(5):
        events.append(
            {"item_id": f"item_{i}", "event_type": "view", "timestamp": 1719876543000 + i * 100}
        )
        events.append(
            {"item_id": f"item_{i}", "event_type": "view", "timestamp": 1719876543000 + i * 100 + 1}
        )
    assert_202(client.post("/events", json={"events": events}))

    resp = assert_json_200(client.get("/trending", params={"window": "1h", "k": 10}))
    assert len(resp["items"]) <= 10


def test_trending_includes_all_fields(client):
    """Trending response has expected top-level fields."""
    resp = assert_json_200(client.get("/trending", params={"window": "1h", "k": 5}))
    assert "items" in resp
    assert resp["window"] == "1h"
    assert resp["k"] == 5
    assert "updated_at" in resp
    for item in resp["items"]:
        assert "item_id" in item
        assert "count" in item
        assert "rank" in item
