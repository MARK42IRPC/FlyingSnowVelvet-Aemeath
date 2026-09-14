"""工作台「办公模式」配置页：只保留配置项，任务界面在独立办公窗口里。"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="office-mode-page-test-")
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
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from config.scale import scale_px
from lib.script.office import plugins as office_plugins
from lib.script.office import skills as office_skills
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES
from lib.script.ui.ai_settings_storage import load_ai_values
from lib.script.ui.office_manager_card import VISIBLE_ROWS, OfficeManagerCard
from lib.script.ui.office_mode_page import OPEN_OFFICE_HINT, OfficeModePage
from lib.script.ui.office_page import OfficeWorkbenchPage
from lib.script.workbench.builtin_pages import builtin_tool_page_specs
from lib.script.workbench.theme import get_workbench_colors


class OfficeModePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        state = Path(_TEST_HOME) / "user" / "state" / "office"
        shutil.rmtree(state, ignore_errors=True)

    def _page(self) -> OfficeModePage:
        page = OfficeModePage(embedded=True)
        self.addCleanup(page.deleteLater)
        return page

    def test_builtin_office_page_is_the_config_only_page(self):
        spec = next(spec for spec in builtin_tool_page_specs() if spec.page_id == "office")
        page = spec.factory()
        self.addCleanup(page.deleteLater)

        self.assertIsInstance(page, OfficeModePage)
        # 任务界面不在这页上：没有任务历史、推理视图和提问框。
        self.assertNotIn("OfficeWorkbenchPage", page.objectName())
        self.assertIsNone(page.findChild(QWidget, "OfficeTaskHistory"))
        self.assertIsNone(page.findChild(QWidget, "OfficePrompt"))
        self.assertIsNone(page.findChild(QWidget, "OfficeMainSplitter"))

    def test_page_scaffold_keeps_office_config_and_manager_cards(self):
        page = self._page()
        section_titles = [
            label.text() for label in page.findChildren(QLabel, "SettingsSectionTitle")
        ]

        self.assertEqual(
            [title for title in section_titles if title],
            ["办公配置", "技能管理", "插件管理"],
        )
        # 配置控件来自共用的 OfficeModeSettings，与 AI 设置面板是同一份实现。
        settings = page._office_settings
        self.assertEqual(settings.backend.itemData(0), "dsh")
        self.assertIsNotNone(settings.use_independent_api)
        self.assertIsNotNone(settings.warmup_on_startup)
        self.assertIsNotNone(page._save_button)
        self.assertEqual(page._save_button.text(), "保存办公配置")
        self.assertTrue(bool(page._save_button.property("primary")))

    def test_page_keeps_a_button_that_opens_the_standalone_office_page(self):
        page = self._page()
        from lib.script.ui import office_page

        self.assertEqual(page._open_button.text(), "打开办公页面")
        self.assertEqual(page._open_button.toolTip(), OPEN_OFFICE_HINT)
        self.addCleanup(office_page.cleanup_office_window)

        page._open_button.click()

        window = office_page.get_office_window()
        self.assertIsNotNone(window)
        self.assertIsInstance(window, OfficeWorkbenchPage)
        self.assertFalse(window.is_embedded)
        self.assertEqual(window.windowTitle(), "办公页面")
        self.assertTrue(window.isVisible())
        self.assertTrue(window._poll_timer.isActive())

    def test_saving_writes_only_the_office_fields(self):
        page = self._page()
        before = load_ai_values(AI_DEFAULT_VALUES)
        page._office_settings.use_independent_api.setChecked(True)
        page._office_settings.api_base_url.setText("https://api.deepseek.com/v1")
        page._office_settings.api_model.setCurrentText("deepseek-chat")
        page._office_settings.warmup_on_startup.setChecked(False)

        self.assertTrue(page.save_office_config())

        values = load_ai_values(AI_DEFAULT_VALUES)
        self.assertTrue(values["office_use_independent_api"])
        self.assertEqual(values["office_api_base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(values["office_api_model"], "deepseek-chat")
        self.assertFalse(values["office_warmup_on_startup"])
        # 办公页面不碰其它 AI 字段：保存前后应逐字相同（与默认值无关）。
        for key in ("api_model", "memory_context_limit", "force_reply_mode", "api_base_url"):
            with self.subTest(key=key):
                self.assertEqual(values[key], before[key])
        self.assertIn("办公配置已保存", page._status_label.text())

    def test_page_has_skill_and_plugin_manager_cards_with_five_rows(self):
        page = self._page()
        skill_card = page._skill_card
        plugin_card = page._plugin_card

        self.assertIsInstance(skill_card, OfficeManagerCard)
        self.assertIsInstance(plugin_card, OfficeManagerCard)
        self.assertEqual(VISIBLE_ROWS, 5)

        skill_names = {entry.name for entry in skill_card.entries()}
        self.assertIn("fsv-office-workflow", skill_names)
        plugin_names = {entry.name for entry in plugin_card.entries()}
        self.assertIn("@deepseek-ai/dsh-base", plugin_names)

        for card in (skill_card, plugin_card):
            with self.subTest(kind=card._kind):
                self.assertEqual(card.visible_rows, 5)
                self.assertEqual(
                    card.list_widget().height(),
                    card.rows_height(),
                )
                self.assertEqual(card.list_widget().verticalScrollBarPolicy(), Qt.ScrollBarAsNeeded)

    def test_theme_refresh_repaints_with_the_workbench_language(self):
        page = self._page()
        colors = get_workbench_colors()

        self.assertIn(colors.pink, page.styleSheet())
        self.assertIn(colors.cyan, page.styleSheet())

    def test_office_helpers_are_bound_to_the_office_backend_modules(self):
        from lib.script.ui import office_mode_page

        self.assertEqual(office_mode_page.list_office_skills(), office_skills.list_skills())
        self.assertEqual(office_mode_page.list_office_plugins(), office_plugins.list_plugins())


if __name__ == "__main__":
    unittest.main()
