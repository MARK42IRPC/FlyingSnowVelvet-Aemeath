import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="workbench-test-", dir="C:/tmp")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QStackedWidget, QWidget

from config.config import COLORS, UI_THEME
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
        self.assertEqual(window._about_button.text(), "关于")
        self.assertEqual(window._about_menu.objectName(), "WorkbenchAboutMenu")
        self.assertTrue(window._about_button.icon().isNull())
        self.assertEqual(window._about_button.width(), scale_px(70))
        self.assertEqual(window._about_button.height(), scale_px(34))
        self.assertFalse(bool(window._about_button.property("active")))
        about_image = window._about_button.grab().toImage()
        middle_y = about_image.height() // 2
        layer = scale_px(2, min_abs=1)
        self.assertEqual(about_image.pixelColor(0, middle_y).rgb(), COLORS["black"].rgb())
        self.assertEqual(about_image.pixelColor(layer, middle_y).rgb(), COLORS["cyan"].rgb())
        self.assertEqual(about_image.pixelColor(layer * 2, middle_y).rgb(), COLORS["pink"].rgb())
        self.assertIs(window._stack.currentWidget(), window._page_hosts["overview"])
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


if __name__ == "__main__":
    unittest.main()
