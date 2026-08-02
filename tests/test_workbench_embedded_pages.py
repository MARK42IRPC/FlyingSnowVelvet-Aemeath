import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="workbench-embedded-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget

from config.scale import scale_px
from config.config import UI
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.script.bug_tracker import window as bug_tracker_module
from lib.script.gemes.MAIN import manager_window as game_manager_module
from lib.script.workbench.builtin_pages import builtin_tool_page_specs


class _FakeGameRuntime:
    def refresh_available_games(self):
        return None


class _FakeGameService:
    def list_installed_games(self):
        return []

    def inbox_dir(self):
        return Path(_TEST_HOME) / "game_inbox"


class WorkbenchEmbeddedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_game_manager_removes_window_chrome_when_embedded(self):
        with patch.object(
            game_manager_module,
            "get_game_package_service",
            return_value=_FakeGameService(),
        ):
            page = game_manager_module.GameManagerWindow(_FakeGameRuntime())
        page.set_embedded_mode(True)

        self.assertIsInstance(page, QtWorkbenchToolPage)
        self.assertTrue(page.is_embedded)
        self.assertTrue(page._header.isHidden())
        self.assertTrue(page._fun_watermark.isHidden())
        self.assertEqual(page._header.cursor().shape(), Qt.ArrowCursor)
        self.assertEqual(page.minimumWidth(), 0)
        self.assertEqual(page._root_layout.contentsMargins().left(), scale_px(10))
        self.assertEqual(page._open_btn.property("accent"), "cyan")
        self.assertEqual(page._uninstall_btn.property("accent"), "danger")
        self.assertIn("QFrame#ManagerPanel", page.styleSheet())
        self.assertIn("QWidget#GameManagerWindow QLabel", page.styleSheet())
        self.assertNotIn("\n            QWidget {\n", page.styleSheet())

        page.deleteLater()
        self.app.processEvents()

    def test_game_manager_refreshes_theme_styles(self):
        original_theme = UI["workbench_light_theme"]
        try:
            UI["workbench_light_theme"] = False
            with patch.object(
                game_manager_module,
                "get_game_package_service",
                return_value=_FakeGameService(),
            ):
                page = game_manager_module.GameManagerWindow(_FakeGameRuntime())
            UI["workbench_light_theme"] = True
            page.refresh_workbench_theme()
            self.assertIn("rgb(255, 248, 251)", page.styleSheet())
            self.assertIn("rgb(32, 52, 77)", page.styleSheet())
        finally:
            UI["workbench_light_theme"] = original_theme
            page.deleteLater()
            self.app.processEvents()

    def test_bug_tracker_uses_compact_toolbar_when_embedded(self):
        with patch.object(bug_tracker_module.BugTrackerWindow, "_reload_snapshot", lambda self, force=False: None), patch.object(
            bug_tracker_module.BugTrackerWindow,
            "_reload_watermark_texts",
            lambda self: None,
        ):
            page = bug_tracker_module.BugTrackerWindow(embedded=True)

        self.assertIsInstance(page, QtWorkbenchToolPage)
        self.assertTrue(page.is_embedded)
        self.assertTrue(page._title.isHidden())
        self.assertTrue(page._header_source_button.isHidden())
        self.assertTrue(page._header_copy_button.isHidden())
        self.assertFalse(page._header_refresh_button.isHidden())
        self.assertTrue(page._watermark_overlay.isHidden())
        self.assertEqual(page._header.cursor().shape(), Qt.ArrowCursor)
        self.assertFalse(page._content_splitter.childrenCollapsible())
        self.assertEqual(page._root_layout.contentsMargins().left(), scale_px(10))
        self.assertEqual(page._filter_info_btn.property("filterTone"), "info")
        self.assertEqual(page._filter_warn_btn.property("filterTone"), "warn")
        self.assertEqual(page._filter_error_btn.property("filterTone"), "error")
        self.assertTrue(bool(page._export_zip_btn.property("primary")))
        self.assertEqual(page._filter_info_btn.styleSheet(), "")
        self.assertEqual(page._export_zip_btn.styleSheet(), "")
        self.assertIn("QWidget#BugTrackerWindow QLabel", page.styleSheet())
        self.assertNotIn("\n            QWidget {\n", page.styleSheet())

        page.deleteLater()
        self.app.processEvents()

    def test_bug_tracker_refreshes_theme_styles(self):
        original_theme = UI["workbench_light_theme"]
        try:
            UI["workbench_light_theme"] = False
            with patch.object(bug_tracker_module.BugTrackerWindow, "_reload_snapshot", lambda self, force=False: None), patch.object(
                bug_tracker_module.BugTrackerWindow,
                "_reload_watermark_texts",
                lambda self: None,
            ):
                page = bug_tracker_module.BugTrackerWindow(embedded=True)
            UI["workbench_light_theme"] = True
            page.refresh_workbench_theme()
            self.assertIn("rgb(255, 248, 251)", page.styleSheet())
            self.assertIn("rgb(32, 52, 77)", page.styleSheet())
        finally:
            UI["workbench_light_theme"] = original_theme
            page.deleteLater()
            self.app.processEvents()

    def test_builtin_game_page_factory_creates_a_dedicated_embedded_page(self):
        runtime = _FakeGameRuntime()
        embedded_page = QWidget()
        game_spec = next(
            spec for spec in builtin_tool_page_specs() if spec.page_id == "game_manager"
        )

        with patch(
            "lib.script.gemes.MAIN.runtime.get_game_runtime",
            return_value=runtime,
        ), patch(
            "lib.script.gemes.MAIN.manager_window.GameManagerWindow",
            return_value=embedded_page,
        ) as manager_window:
            page = game_spec.factory()

        self.assertIs(page, embedded_page)
        manager_window.assert_called_once_with(runtime, embedded=True)
        embedded_page.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
