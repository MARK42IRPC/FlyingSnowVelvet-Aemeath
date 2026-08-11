from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.app.tray_actions import (
    cleanup_music_cache,
    cleanup_music_history,
    open_author_page,
    set_autostart_enabled,
)


class TrayActionsTests(unittest.TestCase):
    def test_cleanup_music_cache_is_scoped_to_known_platform_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "netease").mkdir()
            (root / "netease" / "song.mp3").write_bytes(b"1234")
            (root / "unrelated").mkdir()
            (root / "unrelated" / "keep.txt").write_text("keep", encoding="utf-8")

            result = cleanup_music_cache(root)

            self.assertTrue(result.success)
            self.assertIn("0.00 MB", result.message)
            self.assertFalse((root / "netease" / "song.mp3").exists())
            self.assertTrue((root / "unrelated" / "keep.txt").exists())

    def test_cleanup_music_history_formats_provider_result(self):
        with patch(
            "lib.script.music.clear_all_history_and_login_data",
            return_value={
                "history_items": 3,
                "deleted_login_files": 1,
                "logged_in_providers": 0,
                "history_failures": 0,
                "failed_login_files": 0,
                "login_provider_failures": 0,
            },
        ):
            result = cleanup_music_history()

        self.assertTrue(result.success)
        self.assertEqual(result.message, "已清空 3 条音乐历史，已清除登录数据")

    def test_author_page_reports_browser_failure(self):
        result = open_author_page(lambda _url: False)

        self.assertFalse(result.success)
        self.assertEqual(result.message, "打开作者主页失败")

    def test_autostart_result_contains_verified_state(self):
        with patch(
            "lib.script.app.autostart.enable_autostart",
            return_value=(True, ""),
        ), patch(
            "lib.script.app.autostart.is_autostart_enabled",
            return_value=True,
        ):
            result = set_autostart_enabled(True)

        self.assertTrue(result.success)
        self.assertTrue(result.enabled)
        self.assertEqual(result.message, "开机启动已启用")


if __name__ == "__main__":
    unittest.main()
