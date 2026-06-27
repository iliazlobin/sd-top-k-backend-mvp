"""FR-1: Event ingestion.

POST /events {events: [{item_id, event_type, timestamp}, ...]} → 202.
Empty batch → 422. Invalid event_type → 422. Missing required fields → 422.
Idempotency: duplicate event_id → no double-count.
"""

import pytest
from verify.acceptance.conftest import assert_202, assert_422


def test_ingest_single_event_success(client):
    """POST /events with one valid event → 202 with accepted=1."""
    body = assert_202(client.post("/events", json={
        "events": [{"item_id": "a", "event_type": "view", "timestamp": 1719876543000}]
    }))
    assert body["accepted"] == 1
    assert body["duplicates"] >= 0
    assert body["blocked"] >= 0


def test_ingest_multiple_events(client):
    """POST /events with a batch of valid events → 202 with accepted count."""
    body = assert_202(client.post("/events", json={
        "events": [
            {"item_id": "a", "event_type": "view", "timestamp": 1719876543000},
            {"item_id": "b", "event_type": "click", "timestamp": 1719876544000},
            {"item_id": "c", "event_type": "mention", "timestamp": 1719876545000},
        ]
    }))
    assert body["accepted"] == 3


def test_ingest_empty_events_422(client):
    """POST /events with empty events array → 422."""
    assert_422(client.post("/events", json={"events": []}))


def test_ingest_invalid_event_type_422(client):
    """POST /events with invalid event_type → 422."""
    assert_422(client.post("/events", json={
        "events": [{"item_id": "a", "event_type": "invalid", "timestamp": 1719876543000}]
    }))


def test_ingest_missing_item_id_422(client):
    """POST /events with missing item_id → 422."""
    assert_422(client.post("/events", json={
        "events": [{"event_type": "view", "timestamp": 1719876543000}]
    }))


def test_ingest_missing_timestamp_422(client):
    """POST /events with missing timestamp → 422."""
    assert_422(client.post("/events", json={
        "events": [{"item_id": "a", "event_type": "view"}]
    }))


def test_ingest_timestamp_zero_or_negative_422(client):
    """POST /events with timestamp ≤ 0 → 422."""
    assert_422(client.post("/events", json={
        "events": [{"item_id": "a", "event_type": "view", "timestamp": 0}]
    }))
    assert_422(client.post("/events", json={
        "events": [{"item_id": "a", "event_type": "view", "timestamp": -1}]
    }))


def test_ingest_missing_events_field_422(client):
    """POST /events with missing events field entirely → 422."""
    assert_422(client.post("/events", json={}))


def test_idempotency_duplicate_event_id(client):
    """Same event_id sent twice → second is duplicate, not double-counted."""
    eid = "11111111-1111-1111-1111-111111111111"
    body1 = assert_202(client.post("/events", json={
        "events": [{"event_id": eid, "item_id": "idem", "event_type": "view",
                     "timestamp": 1719876543000}]
    }))
    assert body1["accepted"] == 1

    body2 = assert_202(client.post("/events", json={
        "events": [{"event_id": eid, "item_id": "idem", "event_type": "view",
                     "timestamp": 1719876543000}]
    }))
    assert body2["accepted"] == 0
    assert body2["duplicates"] >= 1


def test_idempotency_duplicate_within_batch(client):
    """Same event_id twice in same batch → accepted=1, duplicates=1."""
    eid = "22222222-2222-2222-2222-222222222222"
    body = assert_202(client.post("/events", json={
        "events": [
            {"event_id": eid, "item_id": "batch", "event_type": "view",
             "timestamp": 1719876543000},
            {"event_id": eid, "item_id": "batch", "event_type": "view",
             "timestamp": 1719876543000},
        ]
    }))
    assert body["accepted"] == 1
    assert body["duplicates"] >= 1


def test_ingest_with_user_id(client):
    """POST /events with user_id → accepted (dedup field present, not validated)."""
    body = assert_202(client.post("/events", json={
        "events": [{"item_id": "withuser", "event_type": "click",
                     "timestamp": 1719876543000, "user_id": "user123"}]
    }))
    assert body["accepted"] == 1


@pytest.mark.parametrize("batch_size", [101, 200, 1000])
def test_ingest_batch_too_large_422(client, batch_size):
    """POST /events with batch > 100 → 422."""
    events = [{"item_id": f"overflow_{i}", "event_type": "view",
               "timestamp": 1719876543000 + i} for i in range(batch_size)]
    assert_422(client.post("/events", json={"events": events}))
