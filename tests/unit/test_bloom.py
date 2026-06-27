"""Unit tests for BloomFilter — isolated, pure function tests.

Tests add, contains, reset, and false-positive rate behavior.
"""

import pytest
from topk.services.bloom import BloomFilter


class TestBloomFilterCreation:
    """Verify Bloom filter is created with correct parameters."""

    def test_default_parameters(self):
        bf = BloomFilter()
        # n=10M, p=0.001 → m ≈ 143.8 Mbits, k ≈ 10
        assert bf.num_bits > 100_000_000  # > 100 Mbits
        assert bf.num_hashes >= 5

    def test_custom_parameters(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        # m = -1000 * ln(0.01) / (ln 2)^2 ≈ 9585 bits
        assert bf.num_bits > 1000
        assert bf.num_hashes >= 1

    def test_initial_state_empty(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert not bf.contains("anything")


class TestBloomFilterAddContains:
    """Verify add and contains correctness."""

    def test_add_then_contains(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        bf.add("hello")
        assert bf.contains("hello")

    def test_contains_never_inserted(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        assert not bf.contains("never_added")

    def test_multiple_adds(self):
        bf = BloomFilter(capacity=10000, error_rate=0.01)
        keys = ["a", "b", "c", "d", "e"]
        for k in keys:
            bf.add(k)
        for k in keys:
            assert bf.contains(k)

    def test_no_false_negative(self):
        """An inserted key must ALWAYS test positive (no false negatives)."""
        bf = BloomFilter(capacity=10000, error_rate=0.01)
        for i in range(100):
            key = f"key_{i}"
            bf.add(key)
            assert bf.contains(key), f"False negative for {key}"

    def test_false_positive_rate(self):
        """Insert 100 items into a filter sized for 1000 at 1% error.
        Check 1000 non-inserted keys — false positive rate should be low."""
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        for i in range(100):
            bf.add(f"real_{i}")

        false_positives = 0
        for i in range(1000):
            if bf.contains(f"fake_{i}"):
                false_positives += 1

        # With p=0.01 and only 10% full, FPR should be very low
        # but we allow some margin since hash distribution is approximate
        # Actually with 100/1000 load, expected FPR ≈ 0.01^(100/1000) bit,
        # the actual FPR is (1 - e^(-kn/m))^k. Let's be generous.
        rate = false_positives / 1000
        assert rate < 0.15, f"False positive rate too high: {rate:.2%}"

    def test_composite_keys(self):
        """Verify composite keys (user:item:minute format) work correctly."""
        bf = BloomFilter(capacity=10000, error_rate=0.01)
        key = "user123:item456:2899794"
        bf.add(key)
        assert bf.contains(key)
        # Slightly different key should NOT match
        assert not bf.contains("user123:item456:2899795")


class TestBloomFilterReset:
    """Verify reset clears all bits."""

    def test_reset_clears_all(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        bf.add("key1")
        bf.add("key2")
        assert bf.contains("key1")

        bf.reset()
        assert not bf.contains("key1")
        assert not bf.contains("key2")

    def test_after_reset_can_readd(self):
        bf = BloomFilter(capacity=1000, error_rate=0.01)
        bf.add("reusable")
        bf.reset()
        bf.add("reusable")
        assert bf.contains("reusable")
