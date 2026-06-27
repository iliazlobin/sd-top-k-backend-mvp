"""Count-Min Sketch — probabilistic frequency estimator with ε-δ guarantees.

ε = 0.01, δ = 0.001 → w = 272 columns, d = 7 rows.
Uses Python's built-in hash function with different seeds per row.
CMS instances are mergeable: cms1 + cms2 produces a CMS whose estimates
are the sum of the individual estimates.

Storage: d × w int array, ~7,616 bytes per CMS.
Update: O(d) = 7 hash ops per increment.
Estimate: O(d) = 7 hash ops per query.
"""

from __future__ import annotations

import math


class CountMinSketch:
    """Approximate frequency counter with configurable precision.

    Guarantees: estimated_count ≥ true_count with probability (1 - δ).
    Over-count by at most ε * N with probability (1 - δ), where N is
    the total number of increments across all items.
    """

    def __init__(self, epsilon: float = 0.01, delta: float = 0.001) -> None:
        self.epsilon = epsilon
        self.delta = delta

        # d = ⌈ln(1/δ)⌉, w = ⌈e/ε⌉
        self.depth: int = math.ceil(math.log(1.0 / delta))
        self.width: int = math.ceil(math.e / epsilon)

        # 2D array: depth rows × width columns, all zeros
        self._cells: list[list[int]] = [[0] * self.width for _ in range(self.depth)]

    def increment(self, item: str, count: int = 1) -> None:
        """Add `count` to the frequency estimate of `item`."""
        for row in range(self.depth):
            col = self._hash(row, item)
            self._cells[row][col] += count

    def estimate(self, item: str) -> int:
        """Return the estimated count for `item` (guaranteed ≥ true count)."""
        return min(self._cells[row][self._hash(row, item)] for row in range(self.depth))

    def reset(self) -> None:
        """Zero out all cells. Used at minute boundary for oldest bucket."""
        for row in range(self.depth):
            for col in range(self.width):
                self._cells[row][col] = 0

    def merge(self, other: CountMinSketch) -> CountMinSketch:
        """Return a new CMS that is the pointwise sum of self and other.

        Both sketches must have identical depth and width.
        """
        if self.depth != other.depth or self.width != other.width:
            raise ValueError(
                f"Cannot merge CMS of different dimensions: "
                f"({self.depth},{self.width}) vs ({other.depth},{other.width})"
            )
        result = CountMinSketch(self.epsilon, self.delta)
        for row in range(self.depth):
            for col in range(self.width):
                result._cells[row][col] = self._cells[row][col] + other._cells[row][col]
        return result

    def total_count(self) -> int:
        """Return total number of increments (sum of any row)."""
        # Any row sums to total N (each increment hits exactly one col per row)
        return sum(self._cells[0])

    def _hash(self, row: int, item: str) -> int:
        """Hash (row, item) to a column index in [0, width)."""
        return abs(hash((row, item))) % self.width
