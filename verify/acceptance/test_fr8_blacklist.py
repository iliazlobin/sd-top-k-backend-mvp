"""FR-8: Blacklist.

POST /admin/blacklist {item_ids: [...]} → 201 adds items.
Blocked items excluded from Space-Saving + trending output.
GET /admin/blacklist lists blocked items.
DELETE /admin/blacklist removes items.
"""

from verify.acceptance.conftest import assert_201, assert_202, assert_json_200, assert_422


def test_blacklist_add_and_list(client):
    """POST /admin/blacklist adds items; GET returns them."""
    body = assert_201(client.post("/admin/blacklist", json={"item_ids": ["spam_one", "spam_two"]}))
    assert body["added"] == 2
    assert set(body["item_ids"]) == {"spam_one", "spam_two"}

    # List them
    resp = assert_json_200(client.get("/admin/blacklist"))
    assert "spam_one" in resp["item_ids"]
    assert "spam_two" in resp["item_ids"]
    assert resp["count"] >= 2


def test_blacklist_idempotent_add(client):
    """Re-adding same item_id is idempotent — no error."""
    # Add
    assert_201(client.post("/admin/blacklist", json={"item_ids": ["idem_blacklist"]}))
    # Re-add
    body = assert_201(client.post("/admin/blacklist", json={"item_ids": ["idem_blacklist"]}))
    # Should succeed (idempotent), added might be 0 or 1
    assert body["added"] >= 0


def test_blacklist_empty_ids_422(client):
    """Empty item_ids → 422."""
    assert_422(client.post("/admin/blacklist", json={"item_ids": []}))


def test_blacklist_missing_item_ids_422(client):
    """Missing item_ids field → 422."""
    assert_422(client.post("/admin/blacklist", json={}))


def test_blacklisted_item_excluded_from_trending(client):
    """Blacklisted item is ingested but excluded from trending."""
    # Blacklist "bad_item"
    assert_201(client.post("/admin/blacklist", json={"item_ids": ["bad_item"]}))

    # Ingest events for "bad_item" and "good_item" in batches of ≤100
    events = []
    for i in range(200):
        events.append({"item_id": "bad_item", "event_type": "view", "timestamp": 1719876543000 + i})
    for i in range(200):
        events.append(
            {"item_id": "good_item", "event_type": "view", "timestamp": 1719876543000 + 200 + i}
        )

    # Chunk into batches of 100
    for i in range(0, len(events), 100):
        assert_202(client.post("/events", json={"events": events[i : i + 100]}))

    # Trending should include "good_item" but NOT "bad_item"
    trending = assert_json_200(client.get("/trending", params={"window": "1h", "k": 10}))
    item_ids = {item["item_id"] for item in trending["items"]}
    assert "good_item" in item_ids, f"good_item should be in trending: {item_ids}"
    assert "bad_item" not in item_ids, f"bad_item should be EXCLUDED: {item_ids}"

    # But "bad_item" should still have a count (CMS counted it)
    count_resp = assert_json_200(
        client.get("/count", params={"item_id": "bad_item", "window": "1h"})
    )
    assert count_resp["count"] >= 200, (
        f"Blacklisted items still counted in CMS: expected ≥200, got {count_resp['count']}"
    )


def test_blacklist_remove_item(client):
    """DELETE /admin/blacklist removes items; they re-enter trending."""
    # Blacklist then remove
    assert_201(client.post("/admin/blacklist", json={"item_ids": ["temp_spam"]}))

    body = assert_json_200(client.delete("/admin/blacklist", json={"item_ids": ["temp_spam"]}))
    assert body["removed"] >= 1
    assert "temp_spam" in body["item_ids"]

    # Verify removed
    list_resp = assert_json_200(client.get("/admin/blacklist"))
    assert "temp_spam" not in list_resp["item_ids"]

    # Now ingest events for the formerly-blocked item (in batches of ≤100)
    events = [
        {"item_id": "temp_spam", "event_type": "click", "timestamp": 1719876543000 + i}
        for i in range(300)
    ]
    for i in range(0, len(events), 100):
        assert_202(client.post("/events", json={"events": events[i : i + 100]}))

    # Should now appear in trending
    trending = assert_json_200(client.get("/trending", params={"window": "1h", "k": 10}))
    item_ids = {item["item_id"] for item in trending["items"]}
    assert "temp_spam" in item_ids, (
        f"After removal from blacklist, temp_spam should be in trending: {item_ids}"
    )


def test_blacklist_delete_idempotent(client):
    """DELETE /admin/blacklist with non-existent item → 200 (idempotent)."""
    body = assert_json_200(
        client.delete("/admin/blacklist", json={"item_ids": ["never_blacklisted"]})
    )
    assert body["removed"] == 0
