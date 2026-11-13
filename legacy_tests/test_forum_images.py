"""图片字节缓存：落盘往返、容量上限、目录安全与和快照共用的缓存根。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from lib.core import forum_images
from lib.core.forum_images import (
    FORUM_IMAGE_CACHE_MAX_ENTRY_BYTES,
    FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES,
    image_path,
    image_stats,
    images_dir,
    read_image_bytes,
    trim_images,
    write_image_bytes,
)

#: 一张最小的「像样的 PNG」：魔数对就行，缓存只认字节不认内容。
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class ImageCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_roundtrip(self) -> None:
        self.assertTrue(write_image_bytes("a" * 32, PNG))
        self.assertEqual(read_image_bytes("a" * 32), PNG)

    def test_cache_lives_under_the_shared_cache_root(self) -> None:
        path = image_path("a" * 32)
        self.assertEqual(path.parent, images_dir())
        self.assertTrue(str(path).replace("\\", "/").endswith("/cache/forum/images/" + "a" * 32))
        self.assertEqual(image_stats(), {"files": 0, "bytes": 0})

    def test_missing_and_blank_ids_read_as_none(self) -> None:
        self.assertIsNone(read_image_bytes("b" * 32))
        self.assertIsNone(read_image_bytes(""))
        self.assertIsNone(read_image_bytes(None))

    def test_ids_cannot_escape_the_directory(self) -> None:
        path = image_path("../../evil id!")
        self.assertEqual(path.parent, images_dir())
        self.assertNotIn("..", path.name)
        self.assertNotIn(" ", path.name)

    def test_empty_and_oversized_writes_are_refused(self) -> None:
        self.assertFalse(write_image_bytes("a" * 32, b""))
        self.assertFalse(write_image_bytes("", PNG))
        self.assertFalse(write_image_bytes("a" * 32, b"\x00" * (FORUM_IMAGE_CACHE_MAX_ENTRY_BYTES + 1)))
        self.assertEqual(image_stats()["files"], 0)

    def test_stats_count_files_and_bytes(self) -> None:
        write_image_bytes("a" * 32, PNG)
        write_image_bytes("b" * 32, PNG)
        self.assertEqual(image_stats(), {"files": 2, "bytes": len(PNG) * 2})

    def test_trim_is_a_noop_under_budget(self) -> None:
        write_image_bytes("a" * 32, PNG)
        trim_images()
        self.assertEqual(read_image_bytes("a" * 32), PNG)
        self.assertLess(len(PNG), FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES)

    def test_total_budget_drops_the_oldest(self) -> None:
        chunk = b"\x00" * 1000
        with patch.object(forum_images, "FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES", 2500):
            now = time.time()
            for index in range(3):
                ident = f"{index:032x}"
                self.assertTrue(write_image_bytes(ident, chunk))
                stamp = now - (3 - index) * 60
                os.utime(image_path(ident), (stamp, stamp))
            # 写第三张时总量到 3000 字节，超过 2500 的上限，最早那张按时间被丢。
            self.assertEqual(image_stats()["files"], 2)
            self.assertIsNone(read_image_bytes(f"{0:032x}"))
            self.assertEqual(read_image_bytes(f"{2:032x}"), chunk)

    def test_unwritable_directory_reports_failure_instead_of_raising(self) -> None:
        target = Path(self._tmp.name) / "blocked"
        target.write_text("我是个文件不是目录", encoding="utf-8")
        with patch.object(forum_images, "images_dir", return_value=target):
            self.assertFalse(write_image_bytes("a" * 32, PNG))
            self.assertIsNone(read_image_bytes("a" * 32))


if __name__ == "__main__":
    unittest.main()
