"""Trending Service — orchestration layer for the top-K pipeline.

Coordinates Count-Min Sketch, Space-Saving, Bloom filter, sliding window,
blacklist, and Redis cache. Provides:

- process_event(): full event processing pipeline (per event)
- flush_to_redis(): rebuild Redis sorted sets from in-memory state
- get_trending(): read from Redis
- get_count(): read directly from CMS

Also manages the blacklist in-memory set (dual-written with PostgreSQL).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from topk.services.bloom import BloomFilter
from topk.services.cms import CountMinSketch
from topk.services.space_saving import SpaceSaving
from topk.services.window import SlidingWindow

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Redis sorted set keys for different k values
SORTED_SET_KEYS = {10: "topk:1h:10", 100: "topk:1h:100", 1000: "topk:1h:1000"}
MAX_K = 1000
TTL_SECONDS = 120  # 2× refresh interval


class TrendingService:
    """Manages the in-memory top-K state and Redis cache.

    All in-memory state (CMS ring buffer, Space-Saving, Bloom filter,
    blacklist set) lives here. Redis is a read cache refreshed from
    this state.
    """

    def __init__(
        self,
        epsilon: float = 0.01,
        delta: float = 0.001,
        space_saving_capacity: int = 1000,
        bloom_capacity: int = 10_000_000,
        bloom_error_rate: float = 0.001,
    ) -> None:
        # Store parameters for reset
        self._epsilon = epsilon
        self._delta = delta
        self._space_saving_capacity = space_saving_capacity
        self._bloom_capacity = bloom_capacity
        self._bloom_error_rate = bloom_error_rate

        # Core algorithms
        self.window = SlidingWindow(num_buckets=60, epsilon=epsilon, delta=delta)
        self.space_saving = SpaceSaving(capacity=space_saving_capacity)
        self.bloom = BloomFilter(capacity=bloom_capacity, error_rate=bloom_error_rate)

        # Blacklist — in-memory set, dual-written with PostgreSQL
        self.blacklist_set: set[str] = set()

        # Redis client (set after construction, before use)
        self._redis: Redis | None = None

    @property
    def redis(self) -> Redis | None:
        return self._redis

    @redis.setter
    def redis(self, client: Redis | None) -> None:
        self._redis = client

    # ── Event processing ───────────────────────────────────

    def process_event(
        self,
        item_id: str,
        event_type: str,
        timestamp_ms: int,
        user_id: str | None = None,
    ) -> dict:
        """Run the full synchronous pipeline for a single event.

        Returns a dict with keys:
            accepted: bool — whether the event was counted (CMS + SS)
            blocked: bool — whether the item is blacklisted
            deduped: bool — whether the event was dropped by Bloom filter
        """
        # 1. Window rotation (if needed)
        self.window.rotate_if_needed()

        # 2. Bloom filter dedup (per-user, per-item, per-minute)
        if user_id is not None:
            minute_bucket = timestamp_ms // 60000
            bloom_key = f"{user_id}:{item_id}:{minute_bucket}"
            if self.bloom.contains(bloom_key):
                # Already counted this (user, item, minute) → dedup
                return {"accepted": False, "blocked": False, "deduped": True}
            self.bloom.add(bloom_key)

        # 3. Blacklist check
        blocked = item_id in self.blacklist_set

        # 4. CMS increment (always, even for blocked items)
        self.window.increment(item_id, count=1)

        # 5. Space-Saving update (skip if blocked)
        if not blocked:
            self.space_saving.increment(item_id, count=1)

        return {"accepted": True, "blocked": blocked, "deduped": False}

    # ── Redis flush ────────────────────────────────────────

    async def flush_to_redis(self) -> bool:
        """Rebuild all Redis sorted sets from in-memory state.

        Queries Space-Saving for monitored items, gets CMS windowed counts,
        and writes to Redis sorted sets for k ∈ {10, 100, 1000}.

        Returns True on success, False if Redis is unavailable.
        """
        if self._redis is None:
            return False

        try:
            # Get candidates from Space-Saving, compute CMS windowed counts
            candidates = self.space_saving.monitored_items()

            # Build scored list: (item_id, cms_count)
            scored: list[tuple[str, int]] = []
            for item_id in candidates:
                # Skip blacklisted items
                if item_id in self.blacklist_set:
                    continue
                count = self.window.estimate(item_id)
                if count > 0:
                    scored.append((item_id, count))

            if not scored:
                return True  # Nothing to flush, but not an error

            # Write to each k-specific sorted set
            for k, key in SORTED_SET_KEYS.items():
                # Take top max(k, len(scored)) items
                top = sorted(scored, key=lambda x: -x[1])[:k]
                if top:
                    mapping = {item_id: float(count) for item_id, count in top}
                    await self._redis.zadd(key, mapping)
                    # Trim to k items (keep highest scores)
                    await self._redis.zremrangebyrank(key, 0, -(k + 1))
                    # Set TTL
                    await self._redis.expire(key, TTL_SECONDS)

            return True
        except Exception:
            logger.warning("Redis flush failed", exc_info=True)
            return False

    # ── Read paths ─────────────────────────────────────────

    async def get_trending(self, k: int) -> dict | None:
        """Read top-K from Redis sorted set.

        Returns None if the key is missing (→503 stale_data).
        Uses the smallest pre-computed cache key >= k.
        """
        if self._redis is None:
            return None

        # Find the smallest cached key >= k (keys are 10, 100, 1000)
        cache_keys = sorted(SORTED_SET_KEYS.keys())
        cache_k = next((ck for ck in cache_keys if ck >= k), cache_keys[-1])
        key = SORTED_SET_KEYS[cache_k]
        try:
            # ZREVRANGE returns members in descending score order
            members = await self._redis.zrevrange(key, 0, k - 1, withscores=True)
            if not members:
                # Cache reachable but empty (cold start / post-reset): return an
                # empty top-K with 200, not 503. 503 is reserved for genuine
                # unavailability (Redis down / read error) — the None returns
                # guarding self._redis and the except block below.
                return {
                    "items": [],
                    "window": "1h",
                    "k": k,
                    "updated_at": int(time.time() * 1000),
                }

            items = []
            for rank_zero, (item_id, score) in enumerate(members):
                items.append(
                    {
                        "item_id": item_id,
                        "count": int(score),
                        "rank": rank_zero + 1,
                    }
                )

            return {
                "items": items,
                "window": "1h",
                "k": k,
                "updated_at": int(time.time() * 1000),
            }
        except Exception:
            logger.warning("Redis trending read failed", exc_info=True)
            return None

    def get_count(self, item_id: str) -> dict:
        """Read CMS estimate directly (sum of all 60 buckets).

        No Redis involved — pure in-memory CMS read.
        """
        return {
            "item_id": item_id,
            "window": "1h",
            "count": self.window.estimate(item_id),
            "is_approximate": True,
        }

    # ── State reset (for test isolation) ────────────────────

    def reset(self) -> None:
        """Reset all in-memory state to a fresh state.

        Recreates CMS sliding window, Space-Saving, Bloom filter,
        and clears the blacklist set. Redis keys are NOT cleared
        (use reset_redis() for that).
        """
        self.window = SlidingWindow(num_buckets=60, epsilon=self._epsilon, delta=self._delta)
        self.space_saving = SpaceSaving(capacity=self._space_saving_capacity)
        self.bloom = BloomFilter(capacity=self._bloom_capacity, error_rate=self._bloom_error_rate)
        self.blacklist_set.clear()

    async def reset_redis(self) -> None:
        """Clear all top-k Redis sorted set keys."""
        if self._redis is None:
            return
        for key in SORTED_SET_KEYS.values():
            try:
                await self._redis.delete(key)
            except Exception:
                pass
