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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lib.script.workbench.settings import (
    GENERAL_CONFIG_CATEGORIES,
    SettingsPageScaffold,
    create_settings_form,
)
from lib.script.workbench.theme import LIGHT_COLORS, workbench_stylesheet


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

        card = QPushButton(host)
        card.setObjectName("ContributionCardButton")
        card.setMinimumHeight(74)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card_layout = QHBoxLayout(card)
        text_wrap = QWidget(card)
        text_layout = QVBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        name = QLabel("贡献者姓名", text_wrap)
        name.setObjectName("ContributionCardName")
        role = QLabel("负责模块与贡献领域", text_wrap)
        role.setObjectName("ContributionCardRole")
        text_layout.addWidget(name)
        text_layout.addWidget(role)
        card_layout.addWidget(text_wrap)
        host_layout.addWidget(card)

        host.resize(900, 180)
        host.show()
        self.app.processEvents()

        self.assertGreaterEqual(card.height(), 74)
        self.assertGreaterEqual(name.height(), name.sizeHint().height())
        self.assertGreaterEqual(role.height(), role.sizeHint().height())
        self.assertIn("ContributionCardButton", host.styleSheet())

        host.close()
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


if __name__ == "__main__":
    unittest.main()
