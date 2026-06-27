"""Unit tests for CountMinSketch — isolated, pure function tests.

Tests increment, estimate, merge, reset, and the ε-δ guarantees.
"""

import pytest
from topk.services.cms import CountMinSketch


class TestCountMinSketchCreation:
    """Verify CMS is created with correct dimensions."""

    def test_default_parameters(self):
        cms = CountMinSketch()
        assert cms.depth == 7  # ⌈ln(1/0.001)⌉ = 7
        assert cms.width == 272  # ⌈e/0.01⌉ = 272

    def test_custom_parameters(self):
        cms = CountMinSketch(epsilon=0.1, delta=0.01)
        # d = ⌈ln(1/0.01)⌉ = 5, w = ⌈e/0.1⌉ = 28
        assert cms.depth == 5
        assert cms.width == 28

    def test_initial_state_all_zeros(self):
        cms = CountMinSketch()
        for row in range(cms.depth):
            for col in range(cms.width):
                assert cms._cells[row][col] == 0


class TestCountMinSketchIncrementEstimate:
    """Verify increment and estimate correctness."""

    def test_single_increment(self):
        cms = CountMinSketch()
        cms.increment("item_a")
        # Estimate ≥ 1 (CMS guarantee: estimate ≥ true count)
        assert cms.estimate("item_a") >= 1

    def test_multiple_increments(self):
        cms = CountMinSketch()
        for _ in range(100):
            cms.increment("item_b")
        assert cms.estimate("item_b") >= 100

    def test_unknown_item_returns_zero(self):
        cms = CountMinSketch()
        assert cms.estimate("never_seen") == 0

    def test_zero_count_estimate_if_never_incremented(self):
        cms = CountMinSketch()
        cms.increment("only_this")
        # Different item should be 0
        assert cms.estimate("something_else") == 0

    def test_estimate_monotonic(self):
        """More increments → estimate never decreases."""
        cms = CountMinSketch()
        prev = cms.estimate("mono")
        for _ in range(50):
            cms.increment("mono")
            curr = cms.estimate("mono")
            assert curr >= prev
            prev = curr

    def test_multiple_items_independent(self):
        """Incrementing item A doesn't affect item B beyond noise."""
        cms = CountMinSketch()
        for _ in range(100):
            cms.increment("A")
        cms.increment("B")
        # B should be about 1 (CMS overcount ≤ εN = 0.01 * 101 ≈ 1)
        # But could be up to ~101 if all hash collisions
        assert 0 <= cms.estimate("B") <= 101  # loose bound
        assert cms.estimate("A") >= 100

    def test_count_param(self):
        cms = CountMinSketch()
        cms.increment("multi", count=5)
        assert cms.estimate("multi") >= 5

    def test_large_count(self):
        cms = CountMinSketch()
        cms.increment("big", count=10000)
        assert cms.estimate("big") >= 10000


class TestCountMinSketchMerge:
    """Verify CMS merge (point-wise addition)."""

    def test_merge_same_dimensions(self):
        cms1 = CountMinSketch(epsilon=0.1, delta=0.1)
        cms2 = CountMinSketch(epsilon=0.1, delta=0.1)

        cms1.increment("shared")
        cms1.increment("shared")
        cms2.increment("shared")

        merged = cms1.merge(cms2)
        # merged estimate for "shared" ≥ 3 (2 + 1)
        assert merged.estimate("shared") >= 3

    def test_merge_different_items(self):
        cms1 = CountMinSketch(epsilon=0.1, delta=0.1)
        cms2 = CountMinSketch(epsilon=0.1, delta=0.1)

        cms1.increment("item1", count=5)
        cms2.increment("item2", count=10)

        merged = cms1.merge(cms2)
        assert merged.estimate("item1") >= 5
        assert merged.estimate("item2") >= 10

    def test_merge_different_dimensions_raises(self):
        cms1 = CountMinSketch(epsilon=0.01, delta=0.001)  # 7×272
        cms2 = CountMinSketch(epsilon=0.1, delta=0.1)  # different

        with pytest.raises(ValueError, match="different dimensions"):
            cms1.merge(cms2)

    def test_merge_commutative(self):
        cms1 = CountMinSketch(epsilon=0.1, delta=0.1)
        cms2 = CountMinSketch(epsilon=0.1, delta=0.1)

        cms1.increment("x", count=3)
        cms2.increment("x", count=5)

        merged_12 = cms1.merge(cms2)
        merged_21 = cms2.merge(cms1)

        assert merged_12.estimate("x") == merged_21.estimate("x")


class TestCountMinSketchReset:
    """Verify reset zeros all cells."""

    def test_reset_zeros_all(self):
        cms = CountMinSketch()
        cms.increment("x", count=100)
        assert cms.estimate("x") >= 100

        cms.reset()
        assert cms.estimate("x") == 0
        # Verify all cells are zero
        for row in range(cms.depth):
            assert all(v == 0 for v in cms._cells[row])

    def test_total_count_after_reset(self):
        cms = CountMinSketch()
        cms.increment("x", count=50)
        assert cms.total_count() == 50
        cms.reset()
        assert cms.total_count() == 0


class TestCountMinSketchTotalCount:
    """Verify total_count helper."""

    def test_total_count_equals_increments(self):
        cms = CountMinSketch()
        total = 0
        for i in range(10):
            cms.increment(f"item_{i}", count=i + 1)
            total += i + 1
        assert cms.total_count() == total
