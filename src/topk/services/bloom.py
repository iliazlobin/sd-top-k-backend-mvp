"""Bloom Filter — probabilistic deduplication filter.

Capacity n=10M, error rate p=0.001.
Bit array size m ≈ 143.8 Mbits (≈17.2 MB), k ≈ 10 hash functions.

Key format: "{user_id}:{item_id}:{minute_bucket}".
Rebuilt at each minute boundary (reset clears all bits).
"""

from __future__ import annotations

import math
import struct


class BloomFilter:
    """Probabilistic set membership test with configurable false-positive rate.

    Uses a bytearray (m bits) and k independent hash functions derived
    from Python's built-in hash with different seeds.

    add(key) — insert key into the filter.
    contains(key) — test membership (may false-positive, never false-negative).
    reset() — clear all bits (used at minute boundary).
    """

    def __init__(
        self,
        capacity: int = 10_000_000,
        error_rate: float = 0.001,
    ) -> None:
        # m = -n * ln(p) / (ln 2)^2
        m = int(
            -capacity * math.log(error_rate) / (math.log(2) ** 2)
        )
        # k = (m / n) * ln(2)
        k = max(1, int((m / capacity) * math.log(2)))

        self._capacity = capacity
        self._error_rate = error_rate
        self._num_bits = m
        self._num_hashes = k

        # Bit array stored as bytearray (each byte holds 8 bits)
        self._num_bytes = (m + 7) // 8
        self._bits = bytearray(self._num_bytes)

    def add(self, key: str) -> None:
        """Insert `key` into the Bloom filter."""
        for seed in range(self._num_hashes):
            bit = self._hash(seed, key)
            byte_idx = bit // 8
            bit_idx = bit % 8
            self._bits[byte_idx] |= (1 << bit_idx)

    def contains(self, key: str) -> bool:
        """Test whether `key` is in the Bloom filter.

        May return True for keys not inserted (false positive).
        Will NEVER return False for an inserted key.
        """
        for seed in range(self._num_hashes):
            bit = self._hash(seed, key)
            byte_idx = bit // 8
            bit_idx = bit % 8
            if not (self._bits[byte_idx] & (1 << bit_idx)):
                return False
        return True

    def reset(self) -> None:
        """Clear all bits (rebuild at minute boundary)."""
        for i in range(self._num_bytes):
            self._bits[i] = 0

    @property
    def num_bits(self) -> int:
        return self._num_bits

    @property
    def num_hashes(self) -> int:
        return self._num_hashes

    def _hash(self, seed: int, key: str) -> int:
        """Hash (seed, key) to a bit index in [0, num_bits)."""
        # Use struct to mix bytes for better distribution
        h = abs(hash((seed, key)))
        # Double-hash to cover the large bit range
        h2 = abs(hash((~seed, key)))
        return (h + seed * h2) % self._num_bits
