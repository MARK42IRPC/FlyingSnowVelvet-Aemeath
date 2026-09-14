"""托盘菜单的常用入口：雪绒论坛与办公页面都走工作台式独立窗口。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from config.tooltip_config import TOOLTIPS
from lib.core.event.center import EventType
from lib.script.ui.forum_window import cleanup_forum_window
from lib.script.ui.office_page import cleanup_office_window
from lib.script.ui.tray_icon import TrayIcon


class TrayOfficeEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tray = TrayIcon()
        self.tray._create_menu()
        self.addCleanup(self.tray._menu.deleteLater)
        self.addCleanup(cleanup_office_window)
        self.addCleanup(cleanup_forum_window)

    def _action_texts(self) -> list[str]:
        return [action.text() for action in self.tray._menu.actions()]

    def test_menu_lists_the_office_page_next_to_the_forum(self):
        texts = self._action_texts()

        self.assertIn("办公页面", texts)
        self.assertEqual(texts.index("办公页面"), texts.index("雪绒论坛") + 1)
        self.assertIn("tray_office", TOOLTIPS)

    def test_office_action_reuses_the_shared_office_window(self):
        from lib.script.ui import office_page

        action = next(
            item for item in self.tray._menu.actions() if item.text() == "办公页面"
        )
        self.assertEqual(action.toolTip(), TOOLTIPS["tray_office"])

        action.trigger()
        window = office_page.get_office_window()
        self.assertIsNotNone(window)

        action.trigger()
        self.assertIs(office_page.get_office_window(), window)

    def test_office_action_reports_failures_as_information(self):
        published: list[tuple] = []

        # 事件中心是全局单例，必须用 patch 还原，否则会污染后续测试的发布路径。
        with patch.object(
            self.tray._event_center,
            "publish",
            side_effect=lambda event: published.append((event.type, event.data)),
        ), patch(
            "lib.script.ui.office_page.open_office_window",
            side_effect=RuntimeError("窗口不可用"),
        ):
            self.tray._on_office_page()

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0], EventType.INFORMATION)
        self.assertIn("窗口不可用", published[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
