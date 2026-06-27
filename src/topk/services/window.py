"""Sliding Window Manager — 60-bucket ring buffer of Count-Min Sketches.

Each bucket represents one minute of event counts. The window covers
60 minutes total. On minute boundary rotation, the oldest bucket is
zeroed out and becomes the new current bucket.

Uses in-process state only — no Redis, no persistence.
"""

from __future__ import annotations

import time

from topk.services.cms import CountMinSketch


class SlidingWindow:
    """Ring buffer of 60 one-minute CMS buckets.

    Window size: 60 minutes (3,600 seconds).
    Bucket index: minute_number % 60.
    """

    def __init__(
        self,
        num_buckets: int = 60,
        epsilon: float = 0.01,
        delta: float = 0.001,
    ) -> None:
        self.num_buckets = num_buckets
        self.epsilon = epsilon
        self.delta = delta

        # Pre-allocate all CMS buckets
        self._buckets: list[CountMinSketch] = [
            CountMinSketch(epsilon, delta) for _ in range(num_buckets)
        ]

        # Current bucket index (which minute we're writing to)
        self._current_idx: int = 0
        self._last_minute: int = self._current_minute()

    def increment(self, item: str, count: int = 1) -> None:
        """Increment the count for `item` in the current minute bucket."""
        self._buckets[self._current_idx].increment(item, count)

    def estimate(self, item: str) -> int:
        """Return the estimated count for `item` summed across all 60 buckets."""
        return sum(bucket.estimate(item) for bucket in self._buckets)

    def rotate_if_needed(self) -> bool:
        """Check if a minute boundary has been crossed; if so, rotate.

        Returns True if rotation occurred.
        """
        current = self._current_minute()
        if current > self._last_minute:
            self._rotate()
            self._last_minute = current
            return True
        return False

    def _rotate(self) -> None:
        """Advance to next bucket and reset it."""
        self._current_idx = (self._current_idx + 1) % self.num_buckets
        self._buckets[self._current_idx].reset()

    @staticmethod
    def _current_minute() -> int:
        """Return the current minute number (epoch_ms // 60000)."""
        return int(time.time() * 1000) // 60000
