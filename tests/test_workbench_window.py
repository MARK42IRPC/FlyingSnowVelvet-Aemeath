import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="workbench-test-")
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
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QStackedWidget, QWidget

from config.config import COLORS, UI, UI_THEME
import config.user_settings as user_settings
from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.script.ui import workbench_window as workbench_module


class _MemorySettings:
    def __init__(self, *_args, **_kwargs):
        self.values = {}

    def value(self, key, default=None, **_kwargs):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class _FakeControlPanel:
    PAGE_IDS = (
        "ai",
        "ui_anim",
        "behavior_physics",
        "audio_music",
        "scene_objects",
        "system_dispatch",
        "desktop_pet_update",
        "contribution_list",
        "sponsor_author",
    )

    def __init__(self):
        self.pages = {}
        self.load_count = 0
        self.close_callback = None

    def get_workbench_page_specs(self):
        return [(page_id, page_id) for page_id in self.PAGE_IDS]

    def create_workbench_page(self, page_id: str):
        page = self.pages.setdefault(page_id, QWidget())
        return page

    def set_external_close_callback(self, callback):
        self.close_callback = callback

    def load_values(self):
        self.load_count += 1


class _FakeTimingManager:
    def __init__(self):
        self.limits = []

    def set_frame_fps_limit(self, source, fps):
        self.limits.append((source, fps))


class _ThemeAwarePage(QWidget):
    def __init__(self):
        super().__init__()
        self.theme_refreshes = 0

    def refresh_workbench_theme(self):
        self.theme_refreshes += 1


class _RefreshContractPage(QWidget):
    def __init__(self):
        super().__init__()
        self.shared_refreshes = 0
        self.legacy_refreshes = 0

    def refresh_workbench_page(self):
        self.shared_refreshes += 1

    def refresh_games(self):
        self.legacy_refreshes += 1

    def _refresh_now(self):
        self.legacy_refreshes += 1


class WorkbenchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shell_groups_pages_and_lazily_loads_tools(self):
        panel = _FakeControlPanel()
        factory_calls = []

        def make_tool():
            factory_calls.append(True)
            return QWidget()

        extras = (
            ("bug_tracker", "Bug tracker", make_tool),
            ("game_manager", "Game manager", make_tool),
        )
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=list(extras),
            )

        self.assertEqual(len(window.page_registry.all()), 12)
        self.assertEqual(len(window.page_registry.navigation_pages()), 10)
        self.assertEqual(tuple(action.text() for action in window._about_menu.actions()), ("贡献者", "赞助作者"))
        self.assertEqual(
            tuple(label.text() for label in window._group_labels.values()),
            ("工作台", "智能交互", "桌宠与场景", "声音与媒体", "扩展与游戏", "系统与维护"),
        )
        self.assertEqual(
            {label.font().pixelSize() for label in window._group_labels.values()},
            {scale_px(9 * workbench_module._NAV_FONT_SCALE)},
        )
        self.assertEqual(
            {button.font().pixelSize() for button in window._page_buttons},
            {scale_px(11 * workbench_module._NAV_FONT_SCALE)},
        )
        self.assertEqual(window._about_button.text(), "贡献列表")
        self.assertEqual(window._sponsor_button.text(), "赞助按钮")
        self.assertEqual(window._about_menu.objectName(), "WorkbenchAboutMenu")
        self.assertTrue(window._about_button.icon().isNull())
        self.assertEqual(window._about_button.width(), scale_px(88, min_abs=78))
        self.assertEqual(window._about_button.height(), scale_px(34))
        self.assertFalse(bool(window._about_button.property("active")))
        window._about_button.click()
        self.assertEqual(window._page_title_label.text(), "贡献者")
        window._sponsor_button.click()
        self.assertEqual(window._page_title_label.text(), "赞助作者")
        about_image = window._about_button.grab().toImage()
        middle_y = about_image.height() // 2
        layer = scale_px(2, min_abs=1)
        self.assertEqual(about_image.pixelColor(0, middle_y).rgb(), COLORS["black"].rgb())
        self.assertEqual(about_image.pixelColor(layer, middle_y).rgb(), COLORS["cyan"].rgb())
        self.assertEqual(about_image.pixelColor(layer * 2, middle_y).rgb(), COLORS["pink"].rgb())
        self.assertIs(window._stack.currentWidget(), window._page_hosts["sponsor_author"])
        self.assertTrue(all(window._stack.indexOf(host) >= 0 for host in window._page_hosts.values()))
        self.assertEqual(factory_calls, [])

        window._set_current_page("bug_tracker")
        self.assertEqual(len(factory_calls), 1)
        self.assertIn("bug_tracker", window._external_pages)
        self.assertEqual(window._page_title_label.text(), "故障跟踪")
        self.assertIs(window._stack.currentWidget(), window._page_hosts["bug_tracker"])

        window._set_current_page("contribution_list")
        self.assertTrue(bool(window._about_button.property("active")))
        self.assertFalse(any(button.isChecked() for button in window._page_buttons))
        active_image = window._about_button.grab().toImage()
        self.assertEqual(
            active_image.pixelColor(layer * 2, middle_y).rgb(),
            UI_THEME["deep_pink"].rgb(),
        )

        window._search.setText("麦克风")
        self.assertTrue(window._page_buttons_by_id["ai"].isHidden())
        self.assertTrue(window._page_buttons_by_id["bug_tracker"].isHidden())
        self.assertFalse(window._page_buttons_by_id["audio_music"].isHidden())
        window._activate_search_result()
        self.assertEqual(window._page_title_label.text(), "音频与音乐")
        self.assertIs(window._stack.currentWidget(), window._page_hosts["audio_music"])

        window.deleteLater()
        self.app.processEvents()

    def test_theme_config_update_repolishes_workbench(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            UI["workbench_light_theme"] = True
            window._on_config_updated(
                workbench_module.Event(
                    workbench_module.EventType.CONFIG_UPDATED,
                    {"values": {"UI": {"workbench_light_theme": True}}},
                )
            )
            self.assertIn("#fff8fb", window.styleSheet())
            self.assertIn("#20344d", window.styleSheet())
            self.assertEqual(window.font().family(), get_ui_font_family())
            self.assertEqual(window._search.font().family(), get_ui_font_family())
            self.assertTrue(window._theme_toggle.isChecked())
        finally:
            UI["workbench_light_theme"] = original_theme
            window.deleteLater()
            self.app.processEvents()

    def test_external_page_uses_shared_refresh_contract(self):
        panel = _FakeControlPanel()
        page = _RefreshContractPage()
        extras = (("bug_tracker", "Bug tracker", lambda: page),)
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=list(extras),
            )

        window._set_current_page("bug_tracker")

        self.assertEqual(page.shared_refreshes, 1)
        self.assertEqual(page.legacy_refreshes, 0)
        window.deleteLater()
        self.app.processEvents()

    def test_external_theme_refresh_is_deferred_and_visible_page_only(self):
        panel = _FakeControlPanel()
        pages = {}

        def make_page(page_id):
            page = _ThemeAwarePage()
            pages[page_id] = page
            return page

        extras = [
            ("bug_tracker", "Bug tracker", lambda: make_page("bug_tracker")),
            ("game_manager", "Game manager", lambda: make_page("game_manager")),
        ]
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=extras,
            )

        try:
            window._set_current_page("bug_tracker")
            window._set_current_page("game_manager")
            UI["workbench_light_theme"] = not bool(original_theme)
            window._on_config_updated(
                workbench_module.Event(
                    workbench_module.EventType.CONFIG_UPDATED,
                    {"values": {"UI": {"workbench_light_theme": UI["workbench_light_theme"]}}},
                )
            )
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 0)
            self.assertEqual(pages["game_manager"].theme_refreshes, 0)
            self.app.processEvents()
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 0)
            self.assertEqual(pages["game_manager"].theme_refreshes, 1)

            window._set_current_page("bug_tracker")
            self.app.processEvents()
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 1)
        finally:
            UI["workbench_light_theme"] = original_theme
            window.deleteLater()
            self.app.processEvents()

    def test_page_transition_keeps_page_visible_without_graphics_effect(self):
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            window.show_page("overview")
            self.app.processEvents()
            window._set_current_page("ui_anim")
            self.app.processEvents()

            host = window._page_hosts["ui_anim"]
            page = panel.pages["ui_anim"]
            self.assertIsNone(host.graphicsEffect())
            self.assertTrue(host.isVisible())
            self.assertTrue(page.isVisible())
            self.assertEqual(page.windowOpacity(), 1.0)
            self.assertFalse(window._page_transition_overlay.isHidden())
        finally:
            window.deleteLater()
            self.app.processEvents()

    def test_theme_transition_does_not_shift_shell_or_page_geometry(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            window.show_page("ui_anim")
            self.app.processEvents()
            shell_geometry = window._shell.geometry()
            host_geometry = window._page_hosts["ui_anim"].geometry()
            page_geometry = panel.pages["ui_anim"].geometry()

            UI["workbench_light_theme"] = not bool(original_theme)
            window._on_config_updated(
                workbench_module.Event(
                    workbench_module.EventType.CONFIG_UPDATED,
                    {"values": {"UI": {"workbench_light_theme": UI["workbench_light_theme"]}}},
                )
            )
            self.app.processEvents()

            self.assertIsNone(window._shell.graphicsEffect())
            self.assertEqual(window._shell.geometry(), shell_geometry)
            self.assertEqual(window._page_hosts["ui_anim"].geometry(), host_geometry)
            self.assertEqual(panel.pages["ui_anim"].geometry(), page_geometry)
        finally:
            UI["workbench_light_theme"] = original_theme
            window.deleteLater()
            self.app.processEvents()

    def test_visible_workbench_limits_runtime_frame_rate_until_hidden(self):
        panel = _FakeControlPanel()
        timing = _FakeTimingManager()
        with patch.object(workbench_module, "QSettings", _MemorySettings), patch.object(
            workbench_module, "get_timing_manager", return_value=timing
        ):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )
            window.show_page("overview")
            self.app.processEvents()
            window.hide_immediately()
            self.app.processEvents()

        self.assertEqual(
            timing.limits,
            [
                (workbench_module._WORKBENCH_FRAME_LIMIT_SOURCE, 30),
                (workbench_module._WORKBENCH_FRAME_LIMIT_SOURCE, None),
            ],
        )
        window.deleteLater()
        self.app.processEvents()

    def test_theme_toggle_is_left_of_search_and_persists_choice(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            header_layout = window._header.layout()
            self.assertLess(
                header_layout.indexOf(window._theme_toggle),
                header_layout.indexOf(window._search),
            )
            self.assertEqual(window._theme_toggle.text(), "")
            self.assertEqual(window._theme_toggle.accessibleName(), "工作台明暗主题")
            self.assertIn("浅色主题", window._theme_toggle.toolTip())

            with tempfile.TemporaryDirectory() as tmpdir:
                settings_path = Path(tmpdir) / "settings.json"
                with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                    window._theme_toggle.setChecked(True)

            self.assertTrue(UI["workbench_light_theme"])
            self.assertIn("#fff8fb", window.styleSheet())
        finally:
            UI["workbench_light_theme"] = original_theme
            window.deleteLater()
            self.app.processEvents()

    def test_theme_toggle_clicks_from_anywhere_on_custom_track(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            UI["workbench_light_theme"] = False
            window._sync_theme_toggle()
            window.show()
            self.app.processEvents()
            center = window._theme_toggle.rect().center()
            QTest.mouseClick(window._theme_toggle, Qt.LeftButton, pos=center)
            self.app.processEvents()
            self.assertTrue(window._theme_toggle.isChecked())
            self.assertTrue(UI["workbench_light_theme"])
        finally:
            UI["workbench_light_theme"] = original_theme
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
