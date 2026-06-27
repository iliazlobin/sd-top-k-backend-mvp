"""FR-5: Point query.

GET /count?item_id=X&window=1h → returns CMS estimate with is_approximate=true.
Unknown item → count=0. Missing item_id → 422. Invalid window → 422.
"""

from verify.acceptance.conftest import assert_202, assert_422, assert_json_200


def test_count_known_item(client):
    """Feed events for item 'known_item', then GET /count returns count > 0."""
    assert_202(
        client.post(
            "/events",
            json={
                "events": [
                    {"item_id": "known_item", "event_type": "click", "timestamp": 1719876543000 + i}
                    for i in range(50)
                ]
            },
        )
    )

    resp = assert_json_200(client.get("/count", params={"item_id": "known_item", "window": "1h"}))
    assert resp["item_id"] == "known_item"
    assert resp["window"] == "1h"
    assert resp["count"] >= 50, f"CMS estimate ≥ 50, got {resp['count']}"
    assert resp["is_approximate"] is True


def test_count_nonexistent_item_returns_zero(client):
    """Unknown item returns count=0, is_approximate=true."""
    resp = assert_json_200(
        client.get("/count", params={"item_id": "definitely_not_here_xyz", "window": "1h"})
    )
    assert resp["item_id"] == "definitely_not_here_xyz"
    assert resp["count"] == 0
    assert resp["is_approximate"] is True


def test_count_missing_item_id_422(client):
    """GET /count missing item_id → 422."""
    assert_422(client.get("/count", params={"window": "1h"}))


def test_count_invalid_window_422(client):
    """GET /count with invalid window → 422."""
    assert_422(client.get("/count", params={"item_id": "x", "window": "invalid"}))
    assert_422(client.get("/count", params={"item_id": "x", "window": "24h"}))
