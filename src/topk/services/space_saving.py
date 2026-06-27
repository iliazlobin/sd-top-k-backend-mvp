"""Space-Saving — approximate top-K tracker with O(1) amortized updates.

Capacity m=1000. Uses a hash map + sorted bucket list (Stream-Summary).
Tracks frequent items candidates; actual counts are provided by CMS.

Tie-breaking: LRU — among items with the same minimum count, the one inserted
earliest is evicted first. Insertion order tracked via a monotonic counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """A count bucket in the Stream-Summary linked list."""
    count: int
    items: set[str] = field(default_factory=set)
    prev: _Bucket | None = None
    next_: _Bucket | None = None


class SpaceSaving:
    """Approximate top-K tracker (Stream-Summary / Space-Saving).

    Maintains up to `capacity` monitored items. When over capacity,
    the least-frequent item (with LRU tie-breaking) is evicted.
    """

    def __init__(self, capacity: int = 1000) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be ≥ 1, got {capacity}")
        self.capacity = capacity

        # item → bucket mapping
        self._item_to_bucket: dict[str, _Bucket] = {}

        # Sentinels for the doubly-linked list of buckets
        self._head = _Bucket(count=-1)  # sentinel head (never holds items)
        self._tail = _Bucket(count=-1)  # sentinel tail
        self._head.next_ = self._tail
        self._tail.prev = self._head

        # Count=1 bucket lives at head.next_ (created lazily)

        # LRU tie-breaking: monotonic insertion counter
        self._counter: int = 0
        self._insertion_time: dict[str, int] = {}

    def increment(self, item: str, count: int = 1) -> None:
        """Record `count` occurrences of `item`.

        If the item is new and the tracker is at capacity, the minimum-count
        item (LRU tie-break) is evicted and replaced by `item` with its count
        bumped to min_count + 1.
        """
        if count < 1:
            return

        if item in self._item_to_bucket:
            # Item is already monitored — bump its count
            for _ in range(count):
                self._increment_existing(item)
        else:
            # New item
            if len(self._item_to_bucket) < self.capacity:
                # Room available — insert with count = count
                self._insert_new(item, count)
            else:
                # At capacity — evict min and insert with bumped count
                for _ in range(count):
                    self._evict_and_insert(item)

    def _increment_existing(self, item: str) -> None:
        """Move item from its current bucket to the next-higher bucket."""
        bucket = self._item_to_bucket[item]
        new_count = bucket.count + 1

        next_bucket = bucket.next_
        if next_bucket is None or next_bucket.count != new_count:
            # Create a new bucket for count = new_count
            new_bucket = _Bucket(count=new_count)
            self._insert_bucket_after(bucket, new_bucket)
            next_bucket = new_bucket

        # Move item
        bucket.items.discard(item)
        next_bucket.items.add(item)
        self._item_to_bucket[item] = next_bucket

        # Remove empty bucket (but never remove sentinels)
        if not bucket.items and bucket is not self._head and bucket is not self._tail:
            self._remove_bucket(bucket)

    def _insert_new(self, item: str, count: int) -> None:
        """Insert a new item with initial count."""
        # Find or create bucket for `count`
        bucket = self._find_or_create_bucket(count)
        bucket.items.add(item)
        self._item_to_bucket[item] = bucket
        self._insertion_time[item] = self._counter
        self._counter += 1

    def _evict_and_insert(self, item: str) -> None:
        """Evict the minimum-count item (LRU tie-break) and insert `item`
        with count = evicted_count + 1."""
        # Find minimum-count bucket (first non-sentinel after head)
        min_bucket = self._head.next_
        if min_bucket is None or min_bucket is self._tail:
            # No items at all — shouldn't happen if at capacity
            return
        if not min_bucket.items:
            # Empty bucket at head — clean up and retry
            self._remove_bucket(min_bucket)
            self._evict_and_insert(item)
            return

        # Pick victim: LRU among items in min_bucket
        victim = min(
            min_bucket.items,
            key=lambda it: self._insertion_time.get(it, 0),
        )

        # Remove victim
        min_bucket.items.discard(victim)
        del self._item_to_bucket[victim]
        self._insertion_time.pop(victim, None)

        if not min_bucket.items:
            self._remove_bucket(min_bucket)

        # Insert new item with count = min_count + 1
        new_count = min_bucket.count + 1 if min_bucket is not self._tail else 1
        self._insert_new(item, new_count)

    def get_top_k(self, k: int) -> list[tuple[str, int]]:
        """Return the top-k monitored items sorted by count descending.

        Tie-breaking: for items with equal count, LRU (earlier insertion first).
        Returns list of (item_id, count) tuples.
        """
        items: list[tuple[str, int]] = []
        bucket = self._tail.prev
        while bucket is not None and bucket is not self._head:
            for it in bucket.items:
                items.append((it, bucket.count))
            bucket = bucket.prev

        # Sort by count desc, then by insertion time asc (LRU tie-break)
        items.sort(
            key=lambda x: (-x[1], self._insertion_time.get(x[0], 0))
        )
        return items[:k]

    def monitored_items(self) -> set[str]:
        """Return the set of all currently monitored item ids."""
        return set(self._item_to_bucket.keys())

    def __len__(self) -> int:
        return len(self._item_to_bucket)

    # ── Bucket list helpers ────────────────────────────────

    def _find_or_create_bucket(self, count: int) -> _Bucket:
        """Find the bucket for `count`, creating it (sorted) if needed.

        Scans from head, stopping before the tail sentinel.
        New buckets are inserted in sorted order before the tail.
        """
        curr = self._head
        # Stop before tail sentinel (which has count=-1)
        while (
            curr.next_ is not None
            and curr.next_ is not self._tail
            and curr.next_.count < count
        ):
            curr = curr.next_

        if (
            curr.next_ is not None
            and curr.next_ is not self._tail
            and curr.next_.count == count
        ):
            return curr.next_

        # Create and insert after curr (which is either a real bucket or head)
        new_bucket = _Bucket(count=count)
        self._insert_bucket_after(curr, new_bucket)
        return new_bucket

    def _insert_bucket_after(self, before: _Bucket, new_bucket: _Bucket) -> None:
        """Insert new_bucket into the list after `before`."""
        after = before.next_
        new_bucket.prev = before
        new_bucket.next_ = after
        before.next_ = new_bucket
        if after is not None:
            after.prev = new_bucket

    def _remove_bucket(self, bucket: _Bucket) -> None:
        """Remove an empty bucket from the list."""
        if bucket is self._head or bucket is self._tail:
            return
        prev = bucket.prev
        nxt = bucket.next_
        if prev is not None:
            prev.next_ = nxt
        if nxt is not None:
            nxt.prev = prev
