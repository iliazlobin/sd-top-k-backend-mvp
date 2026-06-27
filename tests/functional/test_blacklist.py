"""Functional tests for admin/blacklist CRUD operations.

Tests in-memory blacklist set manipulation via the TrendingService.
Database-dependent tests are in verify/acceptance/.
"""

import pytest
from topk.services.trending import TrendingService


class TestBlacklistInMemory:
    """Test blacklist operations on the in-memory set."""

    @pytest.fixture
    def svc(self):
        return TrendingService()

    def test_add_item(self, svc):
        svc.blacklist_set.add("spam")
        assert "spam" in svc.blacklist_set
        assert len(svc.blacklist_set) == 1

    def test_add_multiple_items(self, svc):
        svc.blacklist_set.add("spam1")
        svc.blacklist_set.add("spam2")
        assert svc.blacklist_set == {"spam1", "spam2"}

    def test_add_idempotent(self, svc):
        svc.blacklist_set.add("dup")
        svc.blacklist_set.add("dup")  # no-op
        assert len(svc.blacklist_set) == 1

    def test_remove_item(self, svc):
        svc.blacklist_set.add("temp")
        svc.blacklist_set.discard("temp")
        assert "temp" not in svc.blacklist_set
        assert len(svc.blacklist_set) == 0

    def test_remove_nonexistent_idempotent(self, svc):
        svc.blacklist_set.discard("ghost")  # no error
        assert len(svc.blacklist_set) == 0

    def test_list_blacklist(self, svc):
        svc.blacklist_set.update(["a", "b", "c"])
        listed = sorted(svc.blacklist_set)
        assert listed == ["a", "b", "c"]


class TestBlacklistEffectOnPipeline:
    """Test that blacklist affects Space-Saving tracking."""

    @pytest.fixture
    def svc(self):
        return TrendingService()

    def test_blacklisted_excluded_from_space_saving(self, svc):
        svc.blacklist_set.add("evil")
        svc.process_event(
            item_id="evil",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        assert "evil" not in svc.space_saving.monitored_items()

    def test_not_blacklisted_included(self, svc):
        svc.process_event(
            item_id="good",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        assert "good" in svc.space_saving.monitored_items()

    def test_reclaim_after_removal(self, svc):
        # Blacklist, then remove, then event should re-enter SS
        svc.blacklist_set.add("rehab")
        svc.process_event(
            item_id="rehab",
            event_type="view",
            timestamp_ms=1719876543000,
        )
        assert "rehab" not in svc.space_saving.monitored_items()

        svc.blacklist_set.discard("rehab")
        svc.process_event(
            item_id="rehab",
            event_type="click",
            timestamp_ms=1719876543000 + 100,
        )
        assert "rehab" in svc.space_saving.monitored_items()
