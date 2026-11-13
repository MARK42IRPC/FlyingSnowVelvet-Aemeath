import unittest

from lib.core.ui_cache import (
    DEFAULT_BUDGET_BYTES,
    MAX_BUDGET_BYTES,
    UiCachePool,
)


class UiCachePoolTests(unittest.TestCase):
    def test_default_budget_is_thirty_mebibytes(self):
        pool = UiCachePool()
        self.assertEqual(pool.budget_bytes, 30 * 1024 * 1024)
        self.assertEqual(DEFAULT_BUDGET_BYTES, 30 * 1024 * 1024)
        self.assertEqual(MAX_BUDGET_BYTES, 30 * 1024 * 1024)

    def test_budget_is_clamped_to_the_hard_ceiling(self):
        pool = UiCachePool(MAX_BUDGET_BYTES * 8)
        self.assertEqual(pool.budget_bytes, MAX_BUDGET_BYTES)
        self.assertEqual(UiCachePool(0).budget_bytes, 1)

    def test_get_refreshes_recency(self):
        pool = UiCachePool(4096)
        pool.store("a", "payload-a", 100)
        pool.store("b", "payload-b", 100)
        pool.store("c", "payload-c", 100)
        self.assertEqual(pool.keys, ("a", "b", "c"))

        self.assertEqual(pool.get("a"), "payload-a")
        self.assertEqual(pool.keys, ("b", "c", "a"))
        self.assertIsNone(pool.get("missing"))
        self.assertTrue(pool.touch("b"))
        self.assertEqual(pool.keys, ("c", "a", "b"))
        self.assertFalse(pool.touch("missing"))

    def test_store_evicts_least_recently_used_entries(self):
        evicted = []
        pool = UiCachePool(250, on_evict=lambda key, payload: evicted.append((key, payload)))
        self.assertTrue(pool.store("a", "payload-a", 100))
        self.assertTrue(pool.store("b", "payload-b", 100))
        self.assertTrue(pool.store("c", "payload-c", 100))

        self.assertEqual(pool.keys, ("b", "c"))
        self.assertEqual(evicted, [("a", "payload-a")])
        self.assertLessEqual(pool.bytes, pool.budget_bytes)
        self.assertEqual(pool.stats.evictions, 1)
        self.assertEqual(pool.stats.entries, 2)

    def test_entry_larger_than_budget_is_rejected(self):
        pool = UiCachePool(500)
        self.assertFalse(pool.store("huge", "payload", 501))
        self.assertEqual(len(pool), 0)
        self.assertEqual(pool.stats.rejected, 1)

    def test_protected_entries_block_eviction_and_reject_the_new_entry(self):
        pool = UiCachePool(200)
        self.assertTrue(pool.store("visible", "payload", 150))

        stored = pool.store("new", "payload-new", 100, protect=lambda key, payload: key == "visible")

        self.assertFalse(stored)
        self.assertEqual(pool.keys, ("visible",))
        self.assertEqual(pool.stats.rejected, 1)
        self.assertEqual(pool.stats.evictions, 0)

    def test_protected_entries_are_skipped_when_others_can_go(self):
        pool = UiCachePool(300)
        pool.store("visible", "payload-visible", 100)
        pool.store("hidden", "payload-hidden", 100)

        stored = pool.store("new", "payload-new", 150, protect=lambda key, payload: key == "visible")

        self.assertTrue(stored)
        self.assertEqual(pool.keys, ("visible", "new"))
        self.assertLessEqual(pool.bytes, pool.budget_bytes)

    def test_restoring_a_key_replaces_the_previous_payload(self):
        evicted = []
        pool = UiCachePool(4096, on_evict=lambda key, payload: evicted.append((key, payload)))
        pool.store("panel", "first", 100)
        pool.store("panel", "second", 120)

        self.assertEqual(pool.keys, ("panel",))
        self.assertEqual(pool.get("panel"), "second")
        self.assertEqual(evicted, [])

    def test_discard_and_clear_notify_evictions(self):
        evicted = []
        pool = UiCachePool(4096, on_evict=lambda key, payload: evicted.append(key))
        pool.store("a", "payload-a", 10)
        pool.store("b", "payload-b", 10)

        self.assertTrue(pool.discard("a"))
        self.assertFalse(pool.discard("a"))
        self.assertEqual(evicted, ["a"])

        pool.clear()
        self.assertEqual(evicted, ["a", "b"])
        self.assertEqual(len(pool), 0)
        self.assertEqual(pool.bytes, 0)

    def test_evict_callback_failures_do_not_break_the_pool(self):
        def boom(_key, _payload):
            raise RuntimeError("release failed")

        pool = UiCachePool(100, on_evict=boom)
        self.assertTrue(pool.store("a", "payload-a", 90))
        self.assertTrue(pool.store("b", "payload-b", 90))
        self.assertEqual(pool.keys, ("b",))

    def test_evict_one_returns_the_key_it_dropped(self):
        pool = UiCachePool(4096)
        pool.store("a", "payload-a", 10)
        pool.store("b", "payload-b", 10)

        self.assertEqual(pool.evict_one(protect=lambda key, payload: key == "a"), "b")
        self.assertIsNone(pool.evict_one(protect=lambda key, payload: True))
        self.assertEqual(pool.keys, ("a",))


if __name__ == "__main__":
    unittest.main()
