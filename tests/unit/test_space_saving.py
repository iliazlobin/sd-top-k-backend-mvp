"""Unit tests for SpaceSaving — isolated, pure function tests.

Tests increment, get_top_k, monitored_items, capacity limits,
LRU tie-breaking, and eviction behavior.
"""

import pytest
from topk.services.space_saving import SpaceSaving


class TestSpaceSavingCreation:
    """Verify SpaceSaving is created with correct state."""

    def test_default_capacity(self):
        ss = SpaceSaving()
        assert ss.capacity == 1000
        assert len(ss) == 0

    def test_custom_capacity(self):
        ss = SpaceSaving(capacity=50)
        assert ss.capacity == 50
        assert len(ss) == 0

    def test_invalid_capacity_raises(self):
        with pytest.raises(ValueError):
            SpaceSaving(capacity=0)
        with pytest.raises(ValueError):
            SpaceSaving(capacity=-1)


class TestSpaceSavingIncrement:
    """Verify increment behavior."""

    def test_single_increment(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("a")
        assert len(ss) == 1
        assert "a" in ss.monitored_items()

    def test_multiple_increments_same_item(self):
        ss = SpaceSaving(capacity=10)
        for _ in range(100):
            ss.increment("hot")
        assert len(ss) == 1
        top = ss.get_top_k(1)
        assert top[0][0] == "hot"
        assert top[0][1] == 100

    def test_increment_with_count(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("bulk", count=50)
        assert len(ss) == 1
        top = ss.get_top_k(1)
        assert top[0][0] == "bulk"
        assert top[0][1] == 50

    def test_zero_count_is_noop(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("nope", count=0)
        assert len(ss) == 0

    def test_negative_count_is_noop(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("nope", count=-1)
        assert len(ss) == 0


class TestSpaceSavingTopK:
    """Verify get_top_k returns correctly sorted results."""

    def test_top_k_basic(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("a", count=100)
        ss.increment("b", count=50)
        ss.increment("c", count=25)

        top = ss.get_top_k(2)
        assert len(top) == 2
        assert top[0][0] == "a"
        assert top[0][1] == 100
        assert top[1][0] == "b"
        assert top[1][1] == 50

    def test_top_k_returns_at_most_k(self):
        ss = SpaceSaving(capacity=10)
        for i in range(5):
            ss.increment(f"item_{i}", count=1)

        top = ss.get_top_k(3)
        assert len(top) == 3

    def test_top_k_when_fewer_items_than_k(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("only")
        top = ss.get_top_k(5)
        assert len(top) == 1
        assert top[0][0] == "only"

    def test_top_k_empty(self):
        ss = SpaceSaving(capacity=10)
        top = ss.get_top_k(5)
        assert len(top) == 0

    def test_top_k_sorted_descending(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("a", count=10)
        ss.increment("b", count=50)
        ss.increment("c", count=30)

        top = ss.get_top_k(3)
        counts = [c for _, c in top]
        assert counts == sorted(counts, reverse=True)

    def test_lru_tie_breaking(self):
        """When counts are equal, earlier-inserted items should appear first
        in get_top_k results (LRU tie-breaking)."""
        ss = SpaceSaving(capacity=10)
        ss.increment("first", count=5)
        ss.increment("second", count=5)
        ss.increment("third", count=5)

        # All have count 5. LRU order: first, second, third.
        top = ss.get_top_k(3)
        # With LRU tie-break (earlier first), order should be first, second, third
        ids = [item[0] for item in top]
        assert ids[0] == "first", f"Expected 'first' first (LRU), got {ids}"
        assert ids[1] == "second", f"Expected 'second' second (LRU), got {ids}"
        assert ids[2] == "third", f"Expected 'third' third (LRU), got {ids}"


class TestSpaceSavingCapacity:
    """Verify capacity enforcement and eviction."""

    def test_exact_capacity(self):
        ss = SpaceSaving(capacity=3)
        ss.increment("a")
        ss.increment("b")
        ss.increment("c")
        assert len(ss) == 3
        assert ss.monitored_items() == {"a", "b", "c"}

    def test_eviction_at_capacity(self):
        """At capacity, least-frequent item should be evicted."""
        ss = SpaceSaving(capacity=3)
        ss.increment("a", count=10)  # heavy
        ss.increment("b", count=5)
        ss.increment("c", count=1)   # light — will be evicted

        # Now add a new item — should evict "c" (count=1)
        ss.increment("d")

        assert len(ss) == 3
        items = ss.monitored_items()
        assert "a" in items  # heavy stays
        assert "b" in items  # middle stays
        # "c" should be gone
        assert "c" not in items, f"Expected 'c' evicted, got {items}"
        assert "d" in items  # new item in

    def test_evicted_item_gets_bumped_count(self):
        """When evicting, the new item inherits evicted count + 1."""
        ss = SpaceSaving(capacity=2)
        ss.increment("a", count=10)
        ss.increment("b", count=1)  # min count = 1

        # Add "c" — evict "b" (count=1), "c" gets count = 1 + 1 = 2
        ss.increment("c")
        top = ss.get_top_k(2)
        # "c" should have count >= 2
        c_count = next(c for item, c in top if item == "c")
        assert c_count >= 2, f"Evicted item should get bumped count ≥ 2, got {c_count}"

    def test_skewed_distribution(self):
        """With heavily skewed data, heavy hitters stay."""
        ss = SpaceSaving(capacity=5)
        # One very heavy hitter
        for _ in range(1000):
            ss.increment("hot")
        # Many light items
        for i in range(100):
            ss.increment(f"cold_{i}")

        top = ss.get_top_k(1)
        assert top[0][0] == "hot", f"Expected 'hot' at #1, got {top[0]}"
        assert top[0][1] >= 1000


class TestSpaceSavingMonitoredItems:
    """Verify monitored_items returns correct set."""

    def test_monitored_items_reflects_state(self):
        ss = SpaceSaving(capacity=10)
        ss.increment("x")
        ss.increment("y")
        assert ss.monitored_items() == {"x", "y"}

    def test_monitored_items_after_eviction(self):
        ss = SpaceSaving(capacity=2)
        ss.increment("a", count=10)
        ss.increment("b", count=5)
        ss.increment("c")  # should evict lowest
        items = ss.monitored_items()
        assert "b" not in items  # b had lower count than a
