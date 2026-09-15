import os
import time
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

from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QPropertyAnimation
from PyQt5.QtGui import QWheelEvent

from config.scale import scale_px
from lib.script.workbench.settings import (
    GENERAL_CONFIG_CATEGORIES,
)
from lib.script.ui.workbench_settings_layout import (
    SettingsPageScaffold,
    create_settings_form,
)
from lib.script.ui.workbench_settings_layout import (
    SmoothScrollArea,
    SETTINGS_FONT_SIZE,
    SETTINGS_LABEL_WIDTH,
)
from lib.script.workbench.theme import LIGHT_COLORS, workbench_stylesheet
from lib.script.ui.ai_settings_panel import (
    AISettingsPanel,
    _AnimationDurationSliderField,
    _ContributionCardButton,
    _GENERAL_DECIMAL_SLIDER_SPECS,
)


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

    def test_startup_page_exposes_ui_cache_toggle_with_a_fitting_label(self):
        with patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)
            page = panel.create_workbench_page('system_dispatch')

        fields = [
            field
            for field in panel._config_tab_meta['system_dispatch']['fields']
            if field.get('dict_name') == 'STARTUP' and field.get('key') == 'ui_cache_preload'
        ]
        self.assertEqual(len(fields), 1)
        self.assertIsInstance(fields[0]['editor'], QCheckBox)

        labels = [
            label
            for label in page.findChildren(QLabel)
            if label.text() == '启动期预绘制缓存'
        ]
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.assertEqual(label.objectName(), 'ConfigFormLabel')
        self.assertLessEqual(
            label.fontMetrics().horizontalAdvance(label.text()),
            SETTINGS_LABEL_WIDTH,
        )

        page.deleteLater()
        panel.deleteLater()
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

    def test_smooth_scroll_area_animates_wheel_scrolling(self):
        """平滑滚动容器把滚轮步进转成短动画：设置页共用这一份手感。"""
        area = SmoothScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)
        for index in range(60):
            layout.addWidget(QLabel(f"行 {index}"))
        area.setWidgetResizable(True)
        area.setWidget(content)
        area.resize(320, 200)
        area.show()
        self.app.processEvents()
        self.addCleanup(area.deleteLater)

        bar = area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        # 单步取设置页的档位；pageStep 由 QScrollArea 按视口高度接管，这里不锁值。
        self.assertEqual(bar.singleStep(), scale_px(24, min_abs=18))

        event = QWheelEvent(
            PyQt5.QtCore.QPointF(10.0, 10.0),
            PyQt5.QtCore.QPointF(10.0, 10.0),
            PyQt5.QtCore.QPoint(0, 0),
            PyQt5.QtCore.QPoint(0, -120),
            PyQt5.QtCore.Qt.NoButton,
            PyQt5.QtCore.Qt.NoModifier,
            PyQt5.QtCore.Qt.NoScrollPhase,
            False,
        )
        # 滚轮事件由视口收，`QAbstractScrollArea` 再转给容器的 `wheelEvent`。
        QApplication.sendEvent(area.viewport(), event)

        self.assertEqual(area._wheel_anim.state(), QPropertyAnimation.Running)
        deadline = time.monotonic() + 2.0
        while bar.value() == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertGreater(bar.value(), 0)

    def test_action_bar_status_slot_sits_left_of_the_buttons(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试设置", "说明")
        save_button = scaffold.add_action("保存更改", lambda: None, primary=True)
        layout = scaffold.action_bar.button_layout
        status_label = scaffold.action_bar.status_label

        self.assertFalse(status_label.isVisibleTo(scaffold.action_bar))

        scaffold.set_status("配置已保存。")

        self.assertTrue(status_label.isVisibleTo(scaffold.action_bar))
        self.assertEqual(status_label.text(), "配置已保存。")
        self.assertLess(layout.indexOf(status_label), layout.indexOf(save_button))

        scaffold.set_status("保存失败。", tone="error")
        self.assertEqual(status_label.property("tone"), "error")

        scaffold.set_status("")
        self.assertFalse(status_label.isVisibleTo(scaffold.action_bar))

        page.deleteLater()
        self.app.processEvents()

    def test_animation_duration_sliders_use_shared_half_to_two_second_range(self):
        self.assertEqual(
            _GENERAL_DECIMAL_SLIDER_SPECS[("ANIMATION", "start_animation_duration")],
            (0.5, 2.0, 0.1, 1),
        )
        self.assertEqual(
            _GENERAL_DECIMAL_SLIDER_SPECS[("ANIMATION", "exit_animation_duration")],
            (0.5, 2.0, 0.1, 1),
        )

        field = _AnimationDurationSliderField(
            0.5,
            2.0,
            0.1,
            value=1.0,
            decimals=1,
            frame_count=120,
            fps=120,
        )
        self.assertEqual(field.text(), "1")
        self.assertEqual(field._value_label.text(), "1.0x/1.0s")
        self.assertGreaterEqual(field._value_label.width(), scale_px(84, min_abs=84))
        field.set_value(0.5)
        self.assertEqual(field._value_label.text(), "0.5x/2.0s")
        field.set_value(2.0)
        self.assertEqual(field._value_label.text(), "2.0x/0.5s")
        field.deleteLater()
        self.app.processEvents()

    def test_animation_speed_descriptions_use_requested_defaults_and_recommendations(self):
        panel = AISettingsPanel(lazy_workbench_pages=True)
        start = panel._build_config_single_description(
            "ANIMATION", "start_animation_duration", 0.5, "启动动画倍速"
        )
        exit_text = panel._build_config_single_description(
            "ANIMATION", "exit_animation_duration", 0.5, "退出动画倍速"
        )
        self.assertIn("默认值: 0.5x，推荐3.0s", start)
        self.assertIn("默认值: 0.5x，推荐默认", exit_text)
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
