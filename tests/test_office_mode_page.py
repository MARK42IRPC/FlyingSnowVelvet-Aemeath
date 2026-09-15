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
from PyQt5.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lib.script.office import plugins as office_plugins
from lib.script.office import skills as office_skills
from lib.script.ui import office_mode_settings as office_settings_module
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES
from lib.script.ui.ai_settings_storage import load_ai_values
from lib.script.ui.office_manager_card import VISIBLE_ROWS, OfficeManagerCard
from lib.script.ui.office_mode_page import (
    OPEN_OFFICE_HINT,
    SAVE_STATUS_HINT,
    OfficeModePage,
)
from lib.script.ui.office_page import OfficeWorkbenchPage
from lib.script.ui.workbench_settings_layout import (
    SETTINGS_FONT_SIZE,
    SETTINGS_LABEL_WIDTH,
    SmoothScrollArea,
)
from lib.script.workbench.builtin_pages import builtin_tool_page_specs
from lib.script.workbench.theme import get_workbench_colors, workbench_stylesheet


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

    def _page_with_probe(self, status: dict) -> OfficeModePage:
        """按给定本机 DSH 探测结果建页（探测只在构造期读一次）。"""
        probe = patch.object(
            office_settings_module, "probe_local_dsh", return_value=dict(status)
        )
        probe.start()
        self.addCleanup(probe.stop)
        return self._page()

    @staticmethod
    def _backend_items(field) -> list[tuple[str, object]]:
        return [(field.itemText(index), field.itemData(index)) for index in range(field.count())]

    @staticmethod
    def _body_size_hint(section) -> int:
        """分区 body 的布局高度：隐藏项必须完全不占位，否则折叠会留下大空白。"""
        layout = section.body_layout
        layout.invalidate()
        layout.activate()
        return layout.sizeHint().height()

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
        # 配置控件来自 OfficeModeSettings；办公配置只长在这一页上。
        settings = page._office_settings
        self.assertEqual(settings.backend.itemData(0), "dsh")
        self.assertIsNotNone(settings.use_independent_api)
        self.assertIsNotNone(settings.warmup_on_startup)
        self.assertIsNotNone(page._save_button)
        self.assertEqual(page._save_button.text(), "保存办公配置")
        self.assertTrue(bool(page._save_button.property("primary")))

    def test_page_uses_the_shared_settings_scaffold_primitives(self):
        """页眉、滚动、底部按钮都跟工作台设置页走同一套原语。"""
        page = self._page()
        scaffold = page._scaffold

        self.assertIsInstance(scaffold.scroll, SmoothScrollArea)
        self.assertTrue(scaffold.action_bar.isVisibleTo(page))
        # 保存按钮在底部动作条里，不在分区正文里——与 AI 设置页一致。
        self.assertIs(page._save_button.parentWidget(), scaffold.action_bar)
        self.assertNotIn(page._save_button, page._config_section.findChildren(QPushButton))
        self.assertIn(page._open_button, page._config_section.findChildren(QPushButton))
        # 「打开办公页面」按设置面板的控件行铺：右对齐标签 + 控件列。
        labels = [
            label.text()
            for label in page._config_section.findChildren(QLabel, "ConfigFormLabel")
        ]
        self.assertIn("办公页面", labels)
        self.assertEqual(page._status_label.text(), SAVE_STATUS_HINT)

    def test_embedded_page_hides_the_in_page_title(self):
        """工作台顶栏已经显示页名，内嵌时页内不再重复大标题（与 AI 设置页一致）。"""
        page = self._page()

        self.assertTrue(page._scaffold.title_label.isHidden())
        self.assertFalse(page._scaffold.description_label.isHidden())

        page.set_embedded_mode(False)

        self.assertFalse(page._scaffold.title_label.isHidden())

    def test_manager_lists_use_the_bold_settings_font(self):
        """列表项字号取设置页档位，字重跟同页正文一样加粗，不再细一档。"""
        page = self._page()

        for card in (page._skill_card, page._plugin_card):
            with self.subTest(kind=card._kind):
                font = card.list_widget().font()
                self.assertEqual(font.pixelSize(), SETTINGS_FONT_SIZE)
                self.assertTrue(font.bold())

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

    # ── 办公后端与本机 DSH ───────────────────────────────────────────

    def test_office_backend_hides_local_entry_when_probe_finds_nothing(self):
        page = self._page_with_probe(
            {"available": False, "reason": "未探测到 本机 DeepSeek Harness"}
        )
        field = page._office_settings.backend

        self.assertEqual(self._backend_items(field), [("DeepSeek Harness（推荐）", "dsh")])
        self.assertEqual(field.currentData(), "dsh")
        self.assertIn("DeepSeek Harness", page._office_settings.backend_description())

    def test_office_backend_lists_local_entry_when_probe_succeeds(self):
        page = self._page_with_probe({
            "available": True,
            "version": "0.1.0-rc.6",
            "source": "npm 全局目录",
            "path": r"C:\Users\demo\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh",
        })
        field = page._office_settings.backend

        self.assertEqual(self._backend_items(field), [
            ("DeepSeek Harness（推荐）", "dsh"),
            ("本机 DeepSeek Harness", "local_dsh"),
        ])
        self.assertTrue(field.model().item(field.findData("local_dsh")).isEnabled())

        field.setCurrentIndex(field.findData("local_dsh"))

        description = page._office_settings.backend_description()
        self.assertIn("0.1.0-rc.6", description)
        self.assertIn("npm 全局目录", description)
        self.assertIn(r"AppData\Roaming\npm", description)

    def test_office_backend_keeps_saved_local_choice_when_probe_finds_nothing(self):
        page = self._page_with_probe(
            {"available": False, "reason": "未探测到 本机 DeepSeek Harness"}
        )
        settings = page._office_settings
        settings.set_values({"office_backend": "local_dsh"})
        field = settings.backend

        self.assertEqual(self._backend_items(field), [
            ("DeepSeek Harness（推荐）", "dsh"),
            ("本机 DeepSeek Harness（未探测到）", "local_dsh"),
        ])
        self.assertFalse(field.model().item(field.findData("local_dsh")).isEnabled())
        self.assertEqual(field.currentData(), "local_dsh")
        self.assertIn("未探测到", settings.backend_description())

    def test_office_backend_does_not_duplicate_local_entry_on_reload(self):
        page = self._page_with_probe({"available": True, "version": "0.1.0-rc.6"})
        settings = page._office_settings

        settings.set_values({"office_backend": "local_dsh"})
        settings.set_values({"office_backend": "dsh"})

        field = settings.backend
        entries = [data for _text, data in self._backend_items(field)]
        self.assertEqual(entries.count("local_dsh"), 1)
        self.assertEqual(field.currentData(), "dsh")

    def test_office_backend_does_not_hide_independent_api_toggle_or_warmup(self):
        page = self._page()
        settings = page._office_settings

        self.assertFalse(settings.backend.isHidden())
        self.assertFalse(settings.use_independent_api.isHidden())
        self.assertFalse(settings.warmup_on_startup.isHidden())
        self.assertTrue(settings.independent_api_group.isHidden())
        self.assertEqual(
            settings.independent_api_form.rowCount(),
            len(settings.independent_api_rows),
        )

        settings.use_independent_api.setChecked(True)

        self.assertFalse(settings.backend.isHidden())
        self.assertFalse(settings.use_independent_api.isHidden())
        self.assertFalse(settings.warmup_on_startup.isHidden())
        self.assertFalse(settings.independent_api_group.isHidden())
        for field in settings.independent_api_rows:
            self.assertFalse(field.isHidden())

    def test_collapsed_office_group_releases_its_row_space(self):
        page = self._page()
        settings = page._office_settings
        section = page._config_section
        group = settings.independent_api_group

        settings.use_independent_api.setChecked(True)
        expanded = self._body_size_hint(section)
        settings.use_independent_api.setChecked(False)
        collapsed = self._body_size_hint(section)

        self.assertAlmostEqual(
            expanded - collapsed,
            group.sizeHint().height() + section.body_layout.spacing(),
            delta=2,
        )

    def test_office_warmup_row_shares_the_office_field_column(self):
        """「启动时预热」独占一张表单，标签列必须与办公分区其它行同宽，否则会左移一列。"""
        page = self._page()
        settings = page._office_settings
        field = settings.warmup_on_startup
        row, role = settings.tail_form.getWidgetPosition(field)

        self.assertEqual(role, QFormLayout.FieldRole)
        label_item = settings.tail_form.itemAt(row, QFormLayout.LabelRole)
        self.assertIsNotNone(label_item, "空标签行不占标签列，会把字段挤到分区最左侧")
        label = label_item.widget()
        self.assertIsInstance(label, QLabel)
        self.assertEqual(label.text(), "")
        self.assertEqual(label.minimumWidth(), SETTINGS_LABEL_WIDTH)
        self.assertEqual(label.maximumWidth(), SETTINGS_LABEL_WIDTH)

        host = QFrame()
        host.setObjectName("WorkbenchPageHost")
        host.setStyleSheet(workbench_stylesheet())
        host_layout = QVBoxLayout(host)
        host_layout.addWidget(page)
        host.resize(1010, 760)
        host.show()
        self.addCleanup(host.close)
        self.addCleanup(host.deleteLater)
        self.app.processEvents()

        section = page._config_section
        for expanded in (False, True):
            with self.subTest(expanded=expanded):
                settings.use_independent_api.setChecked(expanded)
                self.app.processEvents()
                section.body_layout.invalidate()
                section.body_layout.activate()
                self.app.processEvents()
                reference = settings.use_independent_api.mapTo(
                    section, PyQt5.QtCore.QPoint(0, 0)
                ).x()
                warmup = field.mapTo(section, PyQt5.QtCore.QPoint(0, 0)).x()
                self.assertGreater(reference, 0)
                self.assertEqual(warmup, reference, "「启动时预热」必须和办公分区其它字段同列")


if __name__ == "__main__":
    unittest.main()
