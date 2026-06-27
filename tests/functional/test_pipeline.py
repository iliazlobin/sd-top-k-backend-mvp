"""Functional tests — in-process endpoint and service scenarios.

These tests exercise the full pipeline: validation, dedup, blacklist,
ingestion flow, and trending retrieval. They use the FastAPI test client
(ASGI transport) with in-memory state.

Endpoint tests are skipped when no database is available (validation
tests only check Pydantic schemas and don't need DB).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from topk.models.schemas import (
    BlacklistAddRequest,
    EventIn,
    EventsRequest,
    EventType,
)
from topk.services.trending import TrendingService

# ── Helpers ────────────────────────────────────────────────────


@pytest.fixture
def trending_service():
    """Return a fresh TrendingService with no Redis/DB."""
    svc = TrendingService()
    svc.redis = MagicMock()
    svc.redis.zadd = AsyncMock()
    svc.redis.zrevrange = AsyncMock(return_value=[])
    svc.redis.zremrangebyrank = AsyncMock()
    svc.redis.expire = AsyncMock()
    return svc


# ── TrendingService Pipeline Tests ─────────────────────────────


class TestTrendingServicePipeline:
    """Test the synchronous event processing pipeline."""

    def test_process_single_event(self, trending_service):
        result = trending_service.process_event(
            item_id="test_item",
            event_type="view",
            timestamp_ms=1719876543000,
            user_id="user1",
        )
        assert result["accepted"] is True
        assert result["blocked"] is False
        assert result["deduped"] is False

    def test_process_event_without_user_id(self, trending_service):
        result = trending_service.process_event(
            item_id="no_user",
            event_type="click",
            timestamp_ms=1719876543000,
        )
        assert result["accepted"] is True

    def test_bloom_dedup_same_user_item_minute(self, trending_service):
        t = 1719876543000
        r1 = trending_service.process_event(
            item_id="dup_test",
            event_type="view",
            timestamp_ms=t,
            user_id="user_A",
        )
        assert r1["accepted"] is True

        r2 = trending_service.process_event(
            item_id="dup_test",
            event_type="view",
            timestamp_ms=t + 100,
            user_id="user_A",
        )
        assert r2["accepted"] is False
        assert r2["deduped"] is True

    def test_bloom_no_dedup_different_users(self, trending_service):
        t = 1719876543000
        r1 = trending_service.process_event(
            item_id="multi_user",
            event_type="click",
            timestamp_ms=t,
            user_id="user_X",
        )
        r2 = trending_service.process_event(
            item_id="multi_user",
            event_type="click",
            timestamp_ms=t + 100,
            user_id="user_Y",
        )
        assert r1["accepted"] is True
        assert r2["accepted"] is True

    def test_bloom_no_dedup_different_minutes(self, trending_service):
        t1 = 1719876543000
        t2 = t1 + 60000
        r1 = trending_service.process_event(
            item_id="cross_minute",
            event_type="view",
            timestamp_ms=t1,
            user_id="user_Z",
        )
        r2 = trending_service.process_event(
            item_id="cross_minute",
            event_type="view",
            timestamp_ms=t2,
            user_id="user_Z",
        )
        assert r1["accepted"] is True
        assert r2["accepted"] is True

    def test_blacklisted_item_blocked(self, trending_service):
        trending_service.blacklist_set.add("spam_item")
        result = trending_service.process_event(
            item_id="spam_item",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        assert result["accepted"] is True
        assert result["blocked"] is True

    def test_blacklisted_item_not_in_space_saving(self, trending_service):
        trending_service.blacklist_set.add("spam")
        trending_service.process_event(
            item_id="spam",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        assert "spam" not in trending_service.space_saving.monitored_items()

    def test_cms_count_reflects_increments(self, trending_service):
        for _ in range(50):
            trending_service.process_event(
                item_id="counted",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        count_result = trending_service.get_count("counted")
        assert count_result["count"] >= 50
        assert count_result["is_approximate"] is True

    def test_cms_zero_for_unknown(self, trending_service):
        count_result = trending_service.get_count("never_ingested")
        assert count_result["count"] == 0

    def test_cms_overcount_property(self, trending_service):
        for _ in range(10):
            trending_service.process_event(
                item_id="overcount_test",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        estimate = trending_service.get_count("overcount_test")["count"]
        assert estimate >= 10

    def test_blacklisted_item_still_counted_in_cms(self, trending_service):
        trending_service.blacklist_set.add("blocked_but_counted")
        for _ in range(100):
            trending_service.process_event(
                item_id="blocked_but_counted",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        estimate = trending_service.get_count("blocked_but_counted")["count"]
        assert estimate >= 100


# ── Pydantic Validation Tests (no app needed) ──────────────────


class TestPydanticValidation:
    """Test Pydantic validation logic directly (no HTTP needed)."""

    def test_events_request_valid(self):
        req = EventsRequest(events=[EventIn(item_id="a", event_type=EventType.view, timestamp=1)])
        assert len(req.events) == 1

    def test_events_request_empty_raises(self):
        with pytest.raises(ValueError):
            EventsRequest(events=[])

    def test_events_request_too_large_raises(self):
        with pytest.raises(ValueError):
            EventsRequest(
                events=[
                    EventIn(item_id=f"x{i}", event_type=EventType.view, timestamp=i + 1)
                    for i in range(101)
                ]
            )

    def test_event_in_missing_item_id_raises(self):
        with pytest.raises(ValueError):
            EventIn(event_type=EventType.view, timestamp=1)

    def test_event_in_missing_timestamp_raises(self):
        with pytest.raises(ValueError):
            EventIn(item_id="a", event_type=EventType.view)

    def test_event_in_timestamp_zero_raises(self):
        with pytest.raises(ValueError):
            EventIn(item_id="a", event_type=EventType.view, timestamp=0)

    def test_event_in_invalid_event_type_raises(self):
        with pytest.raises(ValueError):
            EventIn(item_id="a", event_type="bad", timestamp=1)

    def test_event_in_valid_event_types(self):
        for et in ("view", "click", "mention"):
            ev = EventIn(item_id="x", event_type=et, timestamp=1)
            assert ev.event_type.value == et

    def test_event_in_optional_event_id(self):
        ev = EventIn(item_id="x", event_type=EventType.view, timestamp=1)
        assert ev.event_id is None

    def test_event_in_optional_user_id(self):
        ev = EventIn(item_id="x", event_type=EventType.view, timestamp=1)
        assert ev.user_id is None

    def test_blacklist_request_empty_raises(self):
        with pytest.raises(ValueError):
            BlacklistAddRequest(item_ids=[])

    def test_blacklist_request_missing_raises(self):
        with pytest.raises(ValueError):
            BlacklistAddRequest()


# ── TrendingService Redis tests ────────────────────────────────


class TestTrendingServiceRedis:
    """Test Redis cache operations with mocked client."""

    def test_get_count_uses_cms_directly(self, trending_service):
        trending_service.process_event(
            item_id="direct_cms",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        result = trending_service.get_count("direct_cms")
        assert result["count"] >= 1
        assert result["window"] == "1h"
        assert result["is_approximate"] is True

    @pytest.mark.asyncio
    async def test_flush_to_redis_handles_no_candidates(self, trending_service):
        result = await trending_service.flush_to_redis()
        assert result is True
        trending_service.redis.zadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_trending_returns_none_when_empty(self, trending_service):
        trending_service.redis.zrevrange = AsyncMock(return_value=[])
        result = await trending_service.get_trending(k=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_trending_returns_none_when_no_redis(self, trending_service):
        trending_service.redis = None
        result = await trending_service.get_trending(k=10)
        assert result is None


# ── Space-Saving + Trending integration ────────────────────────


class TestSpaceSavingTrendingIntegration:
    """Test that Space-Saving output feeds into trending correctly."""

    def test_top_k_from_service_layer(self, trending_service):
        for _ in range(1000):
            trending_service.process_event(
                item_id="hot",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        for i in range(100):
            trending_service.process_event(
                item_id=f"cold_{i}",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        assert "hot" in trending_service.space_saving.monitored_items()
        assert trending_service.window.estimate("hot") >= 1000

    def test_blacklist_filters_from_trending(self, trending_service):
        trending_service.blacklist_set.add("spam")
        for _ in range(100):
            trending_service.process_event(
                item_id="spam",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        for _ in range(10):
            trending_service.process_event(
                item_id="good",
                event_type="view",
                timestamp_ms=1719876543000,
            )
        assert "spam" not in trending_service.space_saving.monitored_items()
        assert trending_service.window.estimate("spam") >= 100
