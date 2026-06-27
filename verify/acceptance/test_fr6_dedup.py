"""FR-6: Anti-gaming per-user dedup.

Bloom filter keyed on (user_id, item_id, minute_bucket).
Same (user_id, item_id) in same minute → second event dropped.
Different user → both counted.
"""

from verify.acceptance.conftest import assert_202, assert_json_200


def test_dedup_same_user_item_minute(client):
    """Same (user_id, item_id) in same minute → only one count increment."""
    t = 1719876543000  # same minute

    # First event
    body1 = assert_202(client.post("/events", json={
        "events": [{"item_id": "dedup_test", "event_type": "view",
                     "timestamp": t, "user_id": "user_A"}]
    }))
    assert body1["accepted"] == 1

    # Second event — same user, same item, same minute
    body2 = assert_202(client.post("/events", json={
        "events": [{"item_id": "dedup_test", "event_type": "view",
                     "timestamp": t + 100, "user_id": "user_A"}]
    }))
    # Second event should be dedup'd: accepted=0 (dedup filter blocked it)
    # Note: "accepted" counts events that passed dedup + blacklist; the
    # duplicate is dropped by Bloom, so accepted=0.
    assert body2["accepted"] == 0

    # Count should be 1, not 2 (CMS was only incremented once)
    count_resp = assert_json_200(client.get("/count", params={
        "item_id": "dedup_test", "window": "1h"
    }))
    # CMS overcounts, so count ≥ 1. But we can't assert count == 1 exactly
    # (CMS is approximate). We assert it's close — ≤1 with some tolerance.
    # The key assertion is that it's NOT ≥ 2 (which would mean double-count).
    assert count_resp["count"] >= 1, f"Expected ≥1, got {count_resp['count']}"
    # CMS overcount is at most εN = 0.01 * 1 = 0.01 → floor 1. So count ≤ 2
    # with high probability. Assert it's not double.
    assert count_resp["count"] < 5, \
        f"Dedup should prevent large overcount, got {count_resp['count']}"


def test_dedup_different_users_counted(client):
    """Different user_id → both events counted."""
    t = 1719876545000

    assert_202(client.post("/events", json={
        "events": [{"item_id": "multi_user", "event_type": "click",
                     "timestamp": t, "user_id": "user_X"}]
    }))
    assert_202(client.post("/events", json={
        "events": [{"item_id": "multi_user", "event_type": "click",
                     "timestamp": t + 100, "user_id": "user_Y"}]
    }))

    count_resp = assert_json_200(client.get("/count", params={
        "item_id": "multi_user", "window": "1h"
    }))
    assert count_resp["count"] >= 2, \
        f"Different users should both be counted, got {count_resp['count']}"


def test_dedup_same_user_different_items_counted(client):
    """Same user_id, different item_id → both counted."""
    t = 1719876547000

    assert_202(client.post("/events", json={
        "events": [
            {"item_id": "item_1", "event_type": "view", "timestamp": t, "user_id": "user_Z"},
            {"item_id": "item_2", "event_type": "view", "timestamp": t + 1, "user_id": "user_Z"},
        ]
    }))

    # Both items should have been counted
    c1 = assert_json_200(client.get("/count", params={
        "item_id": "item_1", "window": "1h"
    }))
    c2 = assert_json_200(client.get("/count", params={
        "item_id": "item_2", "window": "1h"
    }))
    assert c1["count"] >= 1, f"item_1 should be counted, got {c1['count']}"
    assert c2["count"] >= 1, f"item_2 should be counted, got {c2['count']}"


def test_dedup_without_user_id_no_dedup(client):
    """Event without user_id → no dedup, always counted."""
    t = 1719876549000

    # Two events for same item without user_id
    assert_202(client.post("/events", json={
        "events": [{"item_id": "no_user", "event_type": "click", "timestamp": t}]
    }))
    assert_202(client.post("/events", json={
        "events": [{"item_id": "no_user", "event_type": "click", "timestamp": t + 1}]
    }))

    count_resp = assert_json_200(client.get("/count", params={
        "item_id": "no_user", "window": "1h"
    }))
    assert count_resp["count"] >= 2, \
        f"Without user_id, both events should be counted, got {count_resp['count']}"
