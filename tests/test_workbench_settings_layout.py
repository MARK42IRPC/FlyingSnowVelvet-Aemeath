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
import lib.script.ui.ai_settings_panel as panel_module
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

    def test_panel_stylesheet_matches_the_shared_visual_module(self):
        """面板的整段 QSS 必须就是描述层生成的那一份（第三轮下沉后的等价断言）。"""
        from lib.core.render.visuals.ai_settings_panel_visuals import ai_settings_panel_stylesheet

        with patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)
        try:
            self.assertEqual(panel.styleSheet(), ai_settings_panel_stylesheet())
        finally:
            panel.deleteLater()
            self.app.processEvents()

    def test_editor_widgets_live_in_the_shared_editor_module(self):
        """配置编辑器族已下沉到 `ai_settings_editors`；面板只按原名转发。

        第四轮把控件类与编辑器工厂切出去后，`AISettingsPanel` 必须仍是这些名字的
        导出面（既有导入路径不变），且两类实现必须同源——面板方法就是混入方法本身。
        """
        import lib.script.ui.ai_settings_editors as editors

        self.assertTrue(issubclass(AISettingsPanel, editors.ConfigEditorMixin))
        self.assertIs(_AnimationDurationSliderField, editors._AnimationDurationSliderField)
        for name in (
            "is_text_editor",
            "is_slider",
            "is_decimal_field",
            "is_check_box",
            "is_combo_box",
            "volume_value_from_percent",
            "_create_volume_slider_editor",
            "_create_animation_folder_duration_editor",
            "_create_path_editor_with_open_button",
            "_create_sequence_editor",
            "_create_compact_pair_editor",
            "_create_config_choice_editor",
            "_create_form_label",
            "_wrap_field_widget",
            "_set_config_editor_value",
            "_set_sequence_editor_values",
            "_volume_percent_from_value",
            "_get_choice_field_options",
            "_get_decimal_slider_spec",
        ):
            self.assertIs(
                getattr(AISettingsPanel, name),
                getattr(editors.ConfigEditorMixin, name),
                name,
            )

    def test_volume_slider_fields_save_through_the_protocol_name(self):
        """音量子页的滑条字段必须能被 `_collect_config_category_values` 读回。

        回归：`ai_settings_config_parse.parse_editor_value` 的协议名字是
        `volume_value_from_percent`，而面板历史上只定义了下划线版
        `_volume_value_from_percent`，因此 `audio_music` 页一旦有 `volume_slider`
        字段，保存路径就会抛 `格式错误: ... has no attribute 'volume_value_from_percent'`。
        编辑器族下沉时一并补上协议别名。
        """
        import lib.script.ui.ai_settings_editors as editors

        self.assertTrue(callable(editors.ConfigEditorMixin.volume_value_from_percent))
        self.assertEqual(editors.ConfigEditorMixin.volume_value_from_percent(50), 0.5)

        with patch.object(AISettingsPanel, '_refresh_hardware_watermark_async', lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)
        try:
            panel.create_workbench_page('audio_music')
            kinds = {field.get('kind') for field in panel._config_tab_meta['audio_music']['fields']}
            self.assertIn('volume_slider', kinds)
            values = panel._collect_config_category_values('audio_music')
            self.assertIn('master_volume', values.get('SOUND', {}))
        finally:
            panel.deleteLater()
            self.app.processEvents()

    def test_config_page_and_external_fields_live_in_the_shared_page_module(self):
        """配置分类页骨架与外部字段族已下沉到 `ai_settings_config_page`。

        第五轮之后 `AISettingsPanel` 只是混入，导出面必须继续指向同一实现；
        `ai_settings_tabs.py` 依赖的 `_build_config_category_panel` 也在这里。
        """
        import lib.script.ui.ai_settings_config_page as config_page

        self.assertTrue(issubclass(AISettingsPanel, config_page.ConfigPageMixin))
        for name in (
            "_build_config_category_panel",
            "_append_autostart_field",
            "_append_announcement_suppression_field",
            "_apply_external_category_fields",
            "_get_autostart_enabled",
            "_set_autostart_enabled",
            "_subscribe_autostart_events",
            "_unsubscribe_autostart_events",
            "_on_autostart_status_change",
            "_get_announcement_forever_suppressed",
            "_set_announcement_forever_suppressed",
        ):
            self.assertIs(
                getattr(AISettingsPanel, name),
                getattr(config_page.ConfigPageMixin, name),
                name,
            )

    def test_ai_page_and_its_value_round_trip_live_in_the_shared_page_module(self):
        """AI 主页面的装配与取值/回填闭环已下沉到 `ai_settings_page`。

        第六轮把 508 行的 `_build_ui` 与本页专属的取值（`_collect_values`）、模型刷新、
        可见性联动、页面动作一起切走。`AISettingsPanel` 只多继承本 mixin，因此下面这些
        名字必须与 `AISettingsPageMixin` 上的实现同源——既有导入路径与直接调用
        （`AISettingsPanel._probe_manual_api_models` 一类）才继续成立。
        """
        import lib.script.ui.ai_settings_page as page

        self.assertTrue(issubclass(AISettingsPanel, page.AISettingsPageMixin))
        for name in (
            "_build_ui",
            "_collect_values",
            "_update_reply_mode_sections",
            "_update_gsv_settings_visibility",
            "_update_gsv_advanced_visibility",
            "_refresh_ollama_model_choices",
            "_refresh_ollama_model_dropdown",
            "_normalize_manual_api_base_url",
            "_normalize_manual_api_base_url_input",
            "_manual_api_models_url",
            "_parse_manual_api_models",
            "_probe_manual_api_models",
            "_refresh_manual_api_model_choices",
            "_on_probe_manual_api_models",
            "_on_open_persona_file",
            "_open_path_with_system_default",
            "_on_voice_package_installed",
            "_on_voice_package_removal_failed",
            "_parse_editor_value",
            "_validate_general_config_values",
            "_validate_ai_values",
            "_collect_all_general_config_values",
            "_apply_all_external_config_fields",
            "_on_restore_ai_defaults",
            "_on_save_ai_action",
            "_on_save_and_restart",
        ):
            panel_attr = getattr(AISettingsPanel, name)
            mixin_attr = getattr(page.AISettingsPageMixin, name)
            if isinstance(panel_attr, classmethod) or hasattr(panel_attr, "__func__"):
                # classmethod 经属性访问会各自绑定到具体类，比底层函数。
                self.assertIs(panel_attr.__func__, mixin_attr.__func__, name)
            else:
                self.assertIs(panel_attr, mixin_attr, name)

        # GPU 档位换算只有一份实现（住在页面模块）；面板按原名转出，`_num_gpu_from_mode`
        # 只被本页的 `_collect_values` 使用，面板不再导出。
        self.assertIn("_num_gpu_from_mode", vars(page))
        self.assertIs(panel_module._gpu_mode_from_num_gpu, page._gpu_mode_from_num_gpu)
        self.assertNotIn("_num_gpu_from_mode", vars(panel_module))

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
