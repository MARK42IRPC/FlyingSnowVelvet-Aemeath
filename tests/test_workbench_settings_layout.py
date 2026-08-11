import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.script.workbench.settings import (
    GENERAL_CONFIG_CATEGORIES,
    SettingsPageScaffold,
    create_settings_form,
)
from lib.script.workbench.settings.page_layout import SETTINGS_FONT_SIZE
from lib.script.workbench.theme import LIGHT_COLORS, workbench_stylesheet
from lib.script.ui.ai_settings_panel import AISettingsPanel, _ContributionCardButton


class WorkbenchSettingsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_schema_keeps_expected_order_and_sections(self):
        page_ids = tuple(page.page_id for page in GENERAL_CONFIG_CATEGORIES)
        self.assertEqual(
            page_ids,
            (
                "ui_anim",
                "behavior_physics",
                "audio_music",
                "scene_objects",
                "system_dispatch",
                "desktop_pet_update",
                "contribution_list",
                "sponsor_author",
            ),
        )
        self.assertEqual(
            tuple(
                (section.config_key, section.title)
                for section in GENERAL_CONFIG_CATEGORIES[0].sections
            ),
            (("ANIMATION", "动画"), ("UI", "界面"), ("COMMAND_DIALOG", "命令框")),
        )

    def test_scaffold_keeps_actions_outside_the_scroll_area(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "响应式页面说明")
        section = scaffold.add_section("基础", "基础字段")
        form = create_settings_form()
        for index in range(20):
            editor = QComboBox() if index == 1 else QLineEdit(str(index))
            form.addRow(f"字段 {index}", editor)
        section.body_layout.addLayout(form)
        scaffold.finish()
        save_button = scaffold.add_action("保存更改", lambda: None, primary=True)

        page.resize(640, 480)
        page.show()
        self.app.processEvents()

        self.assertTrue(scaffold.scroll.isVisible())
        self.assertTrue(scaffold.action_bar.isVisible())
        self.assertIs(scaffold.action_bar.parentWidget(), page)
        self.assertIs(scaffold.scroll.parentWidget(), page)
        self.assertEqual(save_button.objectName(), "SettingsPrimaryAction")
        self.assertEqual(form.rowWrapPolicy(), form.WrapLongRows)
        self.assertGreater(section.height(), scaffold.scroll.viewport().height())
        label_widths = {
            form.itemAt(row, QFormLayout.LabelRole).widget().width()
            for row in range(form.rowCount())
        }
        field_widths = {
            form.itemAt(row, QFormLayout.FieldRole).widget().width()
            for row in range(form.rowCount())
        }
        self.assertEqual(len(label_widths), 1)
        self.assertEqual(len(field_widths), 1)

        page.close()
        page.deleteLater()
        self.app.processEvents()

    def test_contribution_card_keeps_two_text_rows_in_workbench(self):
        host = QFrame()
        host.setObjectName("WorkbenchPageHost")
        host.setStyleSheet(workbench_stylesheet())
        host_layout = QVBoxLayout(host)
        panel = AISettingsPanel(lazy_workbench_pages=True)
        page = panel.create_workbench_page("contribution_list")
        host_layout.addWidget(page)

        host.resize(1000, 760)
        host.show()
        page.show()
        self.app.processEvents()

        cards = page.findChildren(_ContributionCardButton)
        self.assertGreater(len(cards), 0)
        for card in cards:
            for object_name in ("ContributionCardName", "ContributionCardRole"):
                label = card.findChild(QLabel, object_name)
                self.assertIsNotNone(label)
                assert label is not None
                label_top = label.mapTo(card, PyQt5.QtCore.QPoint(0, 0)).y()
                self.assertGreaterEqual(label_top, 0)
                self.assertGreaterEqual(label.height(), label.sizeHint().height())
                self.assertLessEqual(label_top + label.height(), card.height())
        self.assertIn("ContributionCardButton", host.styleSheet())

        host.close()
        panel.deleteLater()
        host.deleteLater()
        self.app.processEvents()

    def test_workbench_theme_stylesheets_have_dark_and_light_palettes(self):
        dark = workbench_stylesheet("dark")
        light = workbench_stylesheet("light")

        self.assertIn("#0d0f12", dark)
        self.assertIn(LIGHT_COLORS.canvas, light)
        self.assertIn(LIGHT_COLORS.text, light)
        self.assertIn(LIGHT_COLORS.pink, light)
        self.assertIn("QWidget#WorkbenchWindow *", light)
        self.assertIn("font-family:", light)
        self.assertNotEqual(dark, light)

    def test_settings_scaffold_uses_readable_ai_scale_for_generated_controls(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "说明")
        section = scaffold.add_section("基础")
        form = create_settings_form()
        field = QLineEdit()
        form.addRow("字段", field)
        section.body_layout.addLayout(form)
        scaffold.finish()

        self.assertEqual(field.font().pixelSize(), SETTINGS_FONT_SIZE)
        self.assertGreaterEqual(field.sizeHint().height(), field.fontMetrics().height())

        page.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
