"""回归：Qt 特效资源缓存必须有 LRU 上限。

`_RESOURCE_CACHE` 只增不减时，长跑（换肤、不同 masked_output_size 的粒子）
会把每条路径的已解码位图都留在内存里。这里锁定上限与淘汰顺序。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lib.core.qt_bridge import effect_system
from lib.core.qt_bridge.effect_system import (
    _RESOURCE_CACHE_MAX,
    _cached_effect_resource,
)


class _FakeResource:
    def __init__(self, name: str) -> None:
        self.name = name


class EffectResourceCacheTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(effect_system._RESOURCE_CACHE)
        effect_system._RESOURCE_CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        effect_system._RESOURCE_CACHE.clear()
        effect_system._RESOURCE_CACHE.update(self._saved)

    def _fill(self, count: int, *, start: int = 0):
        loaded = []

        def fake_load(path, _options):
            loaded.append(path)
            return _FakeResource(path)

        with patch.object(effect_system, "load_effect_resource", side_effect=fake_load):
            for index in range(start, start + count):
                key = (f"p{index}.webp", None, False, 0.12)
                _cached_effect_resource(key, key[0], {})
        return loaded

    def test_cache_never_grows_past_the_configured_limit(self):
        self._fill(_RESOURCE_CACHE_MAX + 25)

        self.assertEqual(len(effect_system._RESOURCE_CACHE), _RESOURCE_CACHE_MAX)

    def test_coldest_entry_is_evicted_first(self):
        self._fill(_RESOURCE_CACHE_MAX + 1)

        keys = list(effect_system._RESOURCE_CACHE)
        self.assertNotIn(("p0.webp", None, False, 0.12), keys)
        self.assertIn(
            (f"p{_RESOURCE_CACHE_MAX}.webp", None, False, 0.12), keys
        )

    def test_hit_refreshes_recency_so_it_survives_the_next_eviction(self):
        self._fill(_RESOURCE_CACHE_MAX)
        hot_key = ("p0.webp", None, False, 0.12)

        with patch.object(effect_system, "load_effect_resource") as loader:
            _cached_effect_resource(hot_key, hot_key[0], {})
        loader.assert_not_called()

        self._fill(1, start=_RESOURCE_CACHE_MAX)

        keys = list(effect_system._RESOURCE_CACHE)
        self.assertIn(hot_key, keys)
        self.assertNotIn(("p1.webp", None, False, 0.12), keys)

    def test_failed_load_is_not_cached(self):
        with patch.object(effect_system, "load_effect_resource", return_value=None):
            result = _cached_effect_resource(("missing.webp", None, False, 0.12), "missing.webp", {})

        self.assertIsNone(result)
        self.assertEqual(len(effect_system._RESOURCE_CACHE), 0)


if __name__ == "__main__":
    unittest.main()
