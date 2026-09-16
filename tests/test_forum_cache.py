"""社区浏览缓存：小快照的读写、容量控制与清理。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core import forum_cache, forum_images
from lib.core.forum_cache import (
    FORUM_CACHE_MAX_ENTRY_BYTES,
    FORUM_CACHE_VERSION,
    cache_dir,
    cache_stats,
    clear_cache,
    drop_entry,
    entry_path,
    read_entry,
    write_entry,
)


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_roundtrip(self) -> None:
        self.assertTrue(write_entry("board-new", {"posts": [{"id": 1}], "total": 1}))
        stored = read_entry("board-new")
        self.assertEqual(stored["total"], 1)
        self.assertEqual(stored["posts"][0]["id"], 1)

    def test_entry_lives_under_the_shared_cache_root(self) -> None:
        path = entry_path("board-new")
        self.assertEqual(path.name, "board-new.json")
        self.assertTrue(str(path).replace("\\", "/").endswith("/cache/forum/board-new.json"))

    def test_entry_names_cannot_escape_the_directory(self) -> None:
        path = entry_path("../../evil name!")
        self.assertEqual(path.parent, cache_dir())
        self.assertNotIn("..", path.name)
        self.assertNotIn(" ", path.name)

    def test_missing_and_broken_entries_read_as_none(self) -> None:
        self.assertIsNone(read_entry("nothing"))
        write_entry("broken", {"a": 1})
        entry_path("broken").write_text("{不是 json", encoding="utf-8")
        self.assertIsNone(read_entry("broken"))

    def test_version_mismatch_is_dropped(self) -> None:
        write_entry("board-new", {"a": 1})
        payload = json.loads(entry_path("board-new").read_text(encoding="utf-8"))
        payload["version"] = FORUM_CACHE_VERSION + 1
        entry_path("board-new").write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(read_entry("board-new"))

    def test_oversized_entries_are_refused(self) -> None:
        huge = {"posts": [{"content": "字" * FORUM_CACHE_MAX_ENTRY_BYTES}]}
        self.assertFalse(write_entry("board-hot", huge))
        self.assertFalse(entry_path("board-hot").exists())

    def test_non_dict_payload_is_refused(self) -> None:
        self.assertFalse(write_entry("board-new", ["不是字典"]))
        self.assertIsNone(read_entry("board-new"))

    def test_drop_entry_removes_one_file(self) -> None:
        write_entry("tags", {"tags": []})
        self.assertTrue(drop_entry("tags"))
        self.assertFalse(entry_path("tags").exists())
        self.assertFalse(drop_entry("tags"))

    def test_stats_count_files_and_bytes(self) -> None:
        self.assertEqual(cache_stats(), {"files": 0, "bytes": 0})
        write_entry("board-new", {"posts": []})
        write_entry("tags", {"tags": []})
        stats = cache_stats()
        self.assertEqual(stats["files"], 2)
        self.assertGreater(stats["bytes"], 0)

    def test_total_budget_drops_the_oldest_entries(self) -> None:
        # 每条约 240 KB：五条超过 1 MiB 的总量上限，最早的那些要按时间被丢掉。
        payload = {"posts": [{"content": "x" * 240_000}]}
        now = time.time()
        for index in range(5):
            name = f"entry-{index}"
            self.assertTrue(write_entry(name, payload))
            stamp = now - (5 - index) * 60
            os.utime(entry_path(name), (stamp, stamp))
        self.assertLessEqual(
            sum(path.stat().st_size for path in cache_dir().glob("*.json")),
            forum_cache.FORUM_CACHE_MAX_TOTAL_BYTES,
        )
        self.assertFalse(entry_path("entry-0").exists())
        self.assertTrue(entry_path("entry-4").exists())

    def test_image_bytes_do_not_evict_the_snapshots(self) -> None:
        write_entry("board-new", {"posts": [{"content": "x" * 240_000}]})
        # 图片另有自己的预算：写进去的字节不该按快照那 1 MiB 的口径把快照挤掉。
        for index in range(4):
            self.assertTrue(forum_images.write_image_bytes(f"{index:032x}", b"\x00" * 300_000))
        self.assertIsNotNone(read_entry("board-new"))
        self.assertEqual(forum_images.image_stats()["files"], 4)

    def test_stats_and_clear_cache_cover_the_image_directory(self) -> None:
        write_entry("board-new", {"posts": []})
        forum_images.write_image_bytes("a" * 32, b"\x00" * 100)
        self.assertEqual(cache_stats(), {"files": 2, "bytes": entry_path("board-new").stat().st_size + 100})
        self.assertEqual(clear_cache().files, 2)
        self.assertFalse(forum_images.images_dir().exists())

    def test_clear_cache_reports_what_it_removed(self) -> None:
        write_entry("board-new", {"posts": []})
        write_entry("tags", {"tags": []})
        nested = cache_dir() / "nested"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "extra.json").write_text("{}", encoding="utf-8")

        report = clear_cache()
        self.assertEqual(report.files, 3)
        self.assertGreater(report.bytes, 0)
        self.assertEqual(report.errors, ())
        self.assertEqual(list(cache_dir().glob("*.json")), [])
        # 空目录顺手回收，缓存根目录保留。
        self.assertFalse(nested.exists())
        self.assertTrue(cache_dir().exists())

    def test_clear_cache_on_an_empty_root_is_a_noop(self) -> None:
        report = clear_cache()
        self.assertEqual((report.files, report.bytes, report.errors), (0, 0, ()))

    def test_unwritable_cache_dir_reports_failure_instead_of_raising(self) -> None:
        target = Path(self._tmp.name) / "blocked"
        target.write_text("我是个文件不是目录", encoding="utf-8")
        with patch.object(forum_cache, "cache_dir", return_value=target):
            self.assertFalse(write_entry("board-new", {"posts": []}))
            self.assertIsNone(read_entry("board-new"))
            self.assertEqual(clear_cache().files, 0)


if __name__ == "__main__":
    unittest.main()