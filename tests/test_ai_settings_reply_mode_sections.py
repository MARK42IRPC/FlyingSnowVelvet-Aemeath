import os
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QComboBox, QLabel

from lib.core.event.center import EventType
from lib.script.ui import ai_settings_config_store as store_module
from lib.script.ui import ai_settings_defaults as defaults_module
from lib.script.ui import ai_settings_page as page_module
from lib.core.services import api_endpoints as api_endpoints_module
from lib.script.ui.ai_settings_panel import AISettingsPanel
from lib.script.gsvmove.package_manager import VoicePackageStatus
from lib.script.workbench.theme import get_workbench_colors, workbench_stylesheet


class AISettingsReplyModeSectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._voice_package_probe = patch.object(
            page_module,
            "get_voice_package_status",
            return_value=VoicePackageStatus("missing", "not installed"),
        )
        self._voice_package_probe.start()
        with patch.object(AISettingsPanel, "_refresh_hardware_watermark_async", lambda self: None):
            self.panel = AISettingsPanel(lazy_workbench_pages=True)

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self._voice_package_probe.stop()

    def _select_mode(self, mode: str) -> None:
        self.panel._force_mode.setCurrentIndex(self.panel._force_mode.findData(mode))
        self.app.processEvents()

    @staticmethod
    def _body_size_hint(section) -> int:
        """分区 body 的布局高度：隐藏项必须完全不占位，否则折叠会留下大空白。"""
        layout = section.body_layout
        layout.invalidate()
        layout.activate()
        return layout.sizeHint().height()

    def test_reply_mode_list_contains_only_direct_routes(self):
        items = [
            (self.panel._force_mode.itemText(index), self.panel._force_mode.itemData(index))
            for index in range(self.panel._force_mode.count())
        ]
        self.assertEqual(items, [
            ("福利 API", "1"),
            ("手动 API", "0"),
            ("本地 Ollama", "2"),
            ("规则回复", "3"),
        ])

    def test_ai_panel_has_no_office_section(self):
        """办公配置只长在办公模式页上：面板里不该再有办公分段或办公控件。"""
        section_titles = [
            label.text() for label in self.panel.findChildren(QLabel, "SettingsSectionTitle")
        ]

        self.assertNotIn("办公模式", section_titles)
        self.assertFalse(hasattr(self.panel, "_office_settings"))
        self.assertFalse(
            [name for name in vars(self.panel) if name.startswith("_office")]
        )
        values = self.panel._collect_values()
        self.assertFalse([key for key in values if key.startswith("office_")])

    def test_auto_companion_interval_slider_uses_minute_limits(self):
        field = self.panel._auto_companion_interval_minutes

        # 默认是福利 API：这一档把间隔钉死在 6 分钟，滑条整行收起。
        self._select_mode("0")
        field.set_value(1)
        self.assertEqual(field.value(), 1)
        self.assertEqual(field._value_label.text(), "1 分钟")
        field.set_value(20)
        self.assertEqual(field.value(), 20)
        self.assertEqual(field._value_label.text(), "20 分钟")

        self.panel._auto_companion_enabled.setChecked(False)
        self.assertFalse(field.isEnabled())
        self.panel._auto_companion_enabled.setChecked(True)
        self.assertTrue(field.isEnabled())

    def test_welfare_mode_pins_the_auto_companion_interval_and_hides_the_slider(self):
        """福利 API 模式下间隔固定 6 分钟：滑条与标签一起收起，不显示任何说明。"""
        field = self.panel._auto_companion_interval_minutes
        form = self.panel._reply_mode_form

        self._select_mode("1")
        self.assertEqual(field.value(), 6)
        self.assertTrue(field.isHidden())
        self.assertTrue(form.labelForField(field).isHidden())
        self.assertEqual(self.panel._collect_values()["auto_companion_interval_minutes"], 6)

        self._select_mode("0")
        self.assertFalse(field.isHidden())
        self.assertFalse(form.labelForField(field).isHidden())

    def test_welfare_mode_does_not_add_an_explanatory_note(self):
        """福利档收起滑条即可，不再补一行说明文字。"""
        self.assertFalse(hasattr(self.panel, "_auto_companion_interval_note"))

        texts = [
            label.text()
            for label in self.panel._reply_mode_form.findChildren(QLabel)
            if label.text()
        ]
        self.assertFalse(
            [text for text in texts if "福利" in text and "自动陪伴" in text],
            f"福利档不应再显示说明行，找到: {texts}",
        )

    def test_welfare_mode_keeps_the_interval_pinned_after_a_slider_write(self):
        """收回福利档后滑条值必须回到 6，否则保存会写进一个用户改不到的数。"""
        field = self.panel._auto_companion_interval_minutes

        self._select_mode("0")
        field.set_value(17)
        self._select_mode("2")
        self.assertEqual(field.value(), 17)
        self._select_mode("1")
        self.assertEqual(field.value(), 6)

    def test_collect_values_includes_auto_companion_interval(self):
        self.panel._auto_companion_interval_minutes.set_value(13)

        values = self.panel._collect_values()

        self.assertEqual(values["auto_companion_interval_minutes"], 13)

    def test_voice_common_settings_stay_open_and_advanced_collapsed(self):
        for field in (
            self.panel._gsv_temperature,
            self.panel._gsv_repetition_penalty,
            self.panel._gsv_speed_factor,
            self.panel._gsv_cache_max_files,
        ):
            with self.subTest(field=field):
                self.assertFalse(field.isHidden())

        self.assertFalse(self.panel._gsv_advanced_toggle.isChecked())
        self.assertTrue(self.panel._gsv_advanced_group.isHidden())
        self.assertEqual(
            self.panel._gsv_advanced_form.rowCount(),
            len(self.panel._gsv_advanced_rows),
        )

        self.panel._gsv_advanced_toggle.setChecked(True)
        self.app.processEvents()
        self.assertFalse(self.panel._gsv_advanced_group.isHidden())

    def test_collapsed_gsv_advanced_group_releases_its_row_space(self):
        section = self.panel._voice_section
        group = self.panel._gsv_advanced_group

        self.panel._gsv_advanced_toggle.setChecked(True)
        expanded = self._body_size_hint(section)
        self.panel._gsv_advanced_toggle.setChecked(False)
        collapsed = self._body_size_hint(section)

        self.assertAlmostEqual(
            expanded - collapsed,
            group.sizeHint().height() + section.body_layout.spacing(),
            delta=2,
        )

    def test_collect_values_omits_removed_voice_settings(self):
        values = self.panel._collect_values()

        self.assertNotIn("gsv_max_steps", values)

    def test_mode_specific_sections_are_hidden_until_selected(self):
        self._select_mode("1")
        self.assertFalse(self.panel._welfare_section.isHidden())
        self.assertTrue(self.panel._manual_api_section.isHidden())
        self.assertTrue(self.panel._ollama_section.isHidden())

        self._select_mode("0")
        self.assertTrue(self.panel._welfare_section.isHidden())
        self.assertFalse(self.panel._manual_api_section.isHidden())
        self.assertTrue(self.panel._ollama_section.isHidden())

        self._select_mode("2")
        self.assertTrue(self.panel._welfare_section.isHidden())
        self.assertTrue(self.panel._manual_api_section.isHidden())
        self.assertFalse(self.panel._ollama_section.isHidden())

    def test_save_and_restart_button_is_to_the_right_and_pink(self):
        layout = self.panel._ai_scaffold.action_bar.button_layout
        save_index = layout.indexOf(self.panel._save_exit_btn)
        restart_index = layout.indexOf(self.panel._save_restart_btn)

        self.assertGreater(restart_index, save_index)
        self.assertEqual(self.panel._save_restart_btn.text(), "保存并重启")
        self.assertEqual(self.panel._save_restart_btn.objectName(), "SettingsRestartAction")
        self.assertTrue(self.panel._save_restart_btn.property("restartAction"))
        stylesheet = workbench_stylesheet()
        self.assertIn("QPushButton#SettingsRestartAction", stylesheet)
        self.assertIn(f"background: {get_workbench_colors().pink}", stylesheet)

    def test_save_failure_does_not_request_restart(self):
        self.panel._on_save = Mock(return_value=False)
        with patch.object(self.panel._ec, "publish") as publish:
            self.panel._on_save_and_restart()

        publish.assert_not_called()
        self.panel._on_save.assert_called_once_with(apply_runtime=False)

    def test_save_success_requests_restart_once(self):
        self.panel._on_save = Mock(return_value=True)
        with patch.object(self.panel._ec, "publish") as publish:
            self.panel._on_save_and_restart()

        publish.assert_called_once()
        self.panel._on_save.assert_called_once_with(apply_runtime=False)
        event = publish.call_args.args[0]
        self.assertEqual(event.type, EventType.APP_QUIT)
        self.assertEqual(event.data, {"exit_code": 0, "restart": True})

    def test_restart_save_skips_runtime_hot_reload(self):
        ai_values = {"force_reply_mode": "1"}
        general_values = {"UI": {"workbench_light_theme": False}}
        future = Future()
        hub = Mock()
        hub.submit_interactive_io.return_value = future
        self.panel._collect_values = Mock(return_value=ai_values)
        self.panel._collect_all_general_config_values = Mock(return_value=general_values)
        self.panel._apply_all_external_config_fields = Mock()
        self.panel._emit_info = Mock()

        # 保存路径已下沉：`_on_save` 现在住在 ai_settings_config_store，patch 打在真正的所有者上。
        with patch.object(store_module, "save_ai_values") as save_ai, patch.object(
            store_module, "_save_general_config"
        ) as save_general, patch.object(store_module, "apply_ai_runtime") as apply_ai, patch.object(
            store_module, "_apply_general_runtime"
        ) as apply_general, patch.object(store_module, "get_compute_hub", return_value=hub):
            saved = self.panel._on_save(apply_runtime=False)
            self.assertTrue(saved)
            self.assertTrue(self.panel._save_task_pending)
            save_ai.assert_not_called()
            save_general.assert_not_called()

            persist = hub.submit_interactive_io.call_args.args[0]
            persist()
            save_ai.assert_called_once()
            save_general.assert_called_once_with(general_values)
            apply_ai.assert_not_called()
            apply_general.assert_not_called()
            future.set_result(None)

        self.assertFalse(self.panel._save_task_pending)
        apply_ai.assert_not_called()
        apply_general.assert_not_called()

    def test_async_save_runs_completion_action_only_after_future_finishes(self):
        future = Future()
        hub = Mock()
        hub.submit_interactive_io.return_value = future
        completion_action = Mock()
        completion = Mock()
        self.panel._save_completion_action = completion_action

        with patch.object(store_module, "get_compute_hub", return_value=hub):
            self.assertTrue(self.panel._submit_save_task(lambda: None, completion))
            self.assertTrue(self.panel._save_task_pending)
            completion_action.assert_not_called()
            completion.assert_not_called()

            future.set_result(None)

        self.assertFalse(self.panel._save_task_pending)
        completion.assert_called_once_with()
        completion_action.assert_called_once_with()
        self.assertIsNone(self.panel._save_completion_action)

    def test_async_save_failure_keeps_panel_open_and_clears_pending_action(self):
        future = Future()
        hub = Mock()
        hub.submit_interactive_io.return_value = future
        completion_action = Mock()
        self.panel._save_completion_action = completion_action

        with patch.object(store_module, "get_compute_hub", return_value=hub):
            self.assertTrue(self.panel._submit_save_task(lambda: None, Mock()))
            future.set_exception(RuntimeError("disk unavailable"))

        self.assertFalse(self.panel._save_task_pending)
        completion_action.assert_not_called()
        self.assertIsNone(self.panel._save_completion_action)

    def test_save_and_exit_does_not_fade_while_save_is_pending(self):
        self.panel.fade_out = Mock()

        def pending_save(*, apply_runtime=True):
            del apply_runtime
            self.panel._save_task_pending = True
            return True

        self.panel._on_save = Mock(side_effect=pending_save)
        self.panel._on_save_and_exit()

        self.panel.fade_out.assert_not_called()
        self.assertTrue(callable(self.panel._save_completion_action))

    def test_repeated_save_while_pending_does_not_replace_completion_action(self):
        original_action = Mock()
        self.panel._save_task_pending = True
        self.panel._save_completion_action = original_action
        self.panel._emit_info = Mock()

        self.panel._on_save_and_exit()

        self.assertIs(self.panel._save_completion_action, original_action)
        self.panel._emit_info.assert_called_once()

    def test_manual_api_model_is_editable_dropdown_with_probe_button(self):
        self.assertIsInstance(self.panel._api_model, QComboBox)
        self.assertTrue(self.panel._api_model.isEditable())
        self.assertEqual(self.panel._probe_manual_api_models_btn.text(), "探测模型")

        self.panel._refresh_manual_api_model_choices("custom-model", ["gpt-5", "qwen3"])

        self.assertEqual(self.panel._api_model.currentText(), "custom-model")
        self.assertEqual(
            [self.panel._api_model.itemData(index) for index in range(self.panel._api_model.count())],
            ["gpt-5", "qwen3"],
        )

    def test_manual_api_address_adds_protocol_and_models_endpoint(self):
        self.assertEqual(
            AISettingsPanel._normalize_manual_api_base_url("api.example.com/v1"),
            "https://api.example.com/v1",
        )
        self.assertEqual(
            AISettingsPanel._normalize_manual_api_base_url("localhost:8080/v1"),
            "http://localhost:8080/v1",
        )
        self.assertEqual(
            AISettingsPanel._manual_api_models_url("api.example.com/v1/chat/completions"),
            "https://api.example.com/v1/models",
        )

    def test_manual_api_provider_preset_fills_address_and_custom_input_is_retained(self):
        provider_index = self.panel._manual_api_provider.findText("DeepSeek")
        self.panel._manual_api_provider.setCurrentIndex(provider_index)
        self.assertEqual(self.panel._api_base_url.text(), "https://api.deepseek.com/v1")

        self.panel._api_base_url.setText("https://gateway.example/v1")
        self.assertEqual(self.panel._manual_api_provider.currentIndex(), 0)
        self.assertEqual(self.panel._api_base_url.text(), "https://gateway.example/v1")

    def test_manual_api_model_probe_parses_models(self):
        response = Mock()
        response.json.return_value = {
            "data": [{"id": "qwen3"}, {"id": "gpt-5"}, {"id": "qwen3"}, {}],
        }
        # HTTP 探测住在 lib.core.services.api_endpoints，面板只做转发，补丁打在真正发请求的模块上。
        with patch.object(api_endpoints_module.requests, "get", return_value=response) as request:
            models = AISettingsPanel._probe_manual_api_models("api.example.com/v1", "secret-key")

        self.assertEqual(models, ["gpt-5", "qwen3"])
        request.assert_called_once_with(
            "https://api.example.com/v1/models",
            headers={"Authorization": "Bearer secret-key"},
            timeout=10.0,
        )
        response.raise_for_status.assert_called_once()

    def test_voice_settings_follow_package_probe(self):
        self.assertFalse(self.panel._gsv_launcher_available)
        self.assertFalse(self.panel._voice_package_banner.isHidden())
        self.assertTrue(self.panel._voice_package_management.isHidden())
        self.assertTrue(self.panel._voice_section.isHidden())
        first_widget = self.panel._ai_scaffold.content_layout.itemAt(0).widget()
        self.assertIs(first_widget, self.panel._voice_package_banner)
        self.assertEqual(self.panel._voice_package_banner.install_button.text(), "安装最新语音包")
        self.assertEqual(
            self.panel._gsv_gpu_hybrid.text(),
            "通用 GPU 加速（DirectML）",
        )

        voice_section = Mock()
        panel = type("GsvPanel", (), {
            "_gsv_launcher_available": True,
            "_voice_section": voice_section,
        })()
        AISettingsPanel._update_gsv_settings_visibility(panel)

        voice_section.setVisible.assert_called_once_with(True)

    def test_nvidia_acceleration_switch_follows_voice_package_availability(self):
        voice_section = Mock()
        checkbox = Mock()
        panel = type("GsvPanel", (), {
            "_gsv_launcher_available": True,
            "_voice_section": voice_section,
            "_gsv_nvidia_cuda_acceleration": checkbox,
        })()

        AISettingsPanel._update_gsv_settings_visibility(panel)
        checkbox.setVisible.assert_called_once_with(True)

        checkbox.reset_mock()
        panel._gsv_launcher_available = False
        AISettingsPanel._update_gsv_settings_visibility(panel)
        checkbox.setVisible.assert_called_once_with(False)

    def test_nvidia_switch_has_no_installer_widget(self):
        self.assertFalse(hasattr(self.panel, "_install_cuda_runtime_button"))
        self.assertEqual(self.panel._gsv_nvidia_cuda_acceleration.text(), "N卡加速")

    def test_nvidia_switch_visibility_follows_the_voice_package_only(self):
        """N 卡开关随语音包可见性显示。

        这里钉住的是现状：面板上曾经有过一条"探测到 N 卡才显示 N 卡加速"的异步能力检查
        （`_refresh_nvidia_acceleration_capability_async` + `_nvidia_gpu_present`），
        实现被删掉后只留下 `has_nvidia_gpu` 导入与两个已无定义的调用点。清理时保留了同一个
        对外表现：开关只由语音包可用性决定，采集也不再看 N 卡探测结果。
        """
        self.assertFalse(hasattr(AISettingsPanel, "_refresh_nvidia_acceleration_capability_async"))
        self.assertFalse(hasattr(self.panel, "_nvidia_gpu_present"))

        checkbox = self.panel._gsv_nvidia_cuda_acceleration
        self.panel._gsv_launcher_available = True
        AISettingsPanel._update_gsv_settings_visibility(self.panel)
        self.assertFalse(checkbox.isHidden())

        self.panel._gsv_launcher_available = False
        AISettingsPanel._update_gsv_settings_visibility(self.panel)
        self.assertTrue(checkbox.isHidden())

    def test_voice_package_install_enables_and_persists_runtime(self):
        saved_values = dict(defaults_module.AI_DEFAULT_VALUES)
        saved_values["gsv_auto_start"] = False
        service = Mock()
        self.panel._gsv_auto_start.setChecked(False)

        # 安装语音包的动作已随 AI 主页面下沉：patch 打在真正的所有者 `ai_settings_page` 上。
        with patch.object(
            page_module, "load_ai_values", return_value=saved_values
        ), patch.object(page_module, "save_ai_values") as save_values, patch.object(
            page_module, "apply_ai_runtime"
        ) as apply_runtime, patch.object(
            page_module,
            "get_voice_package_status",
            return_value=VoicePackageStatus("installed", "ok"),
        ), patch(
            "lib.script.gsvmove.get_gsvmove_service", return_value=service
        ):
            self.panel._on_voice_package_installed()

        persisted = save_values.call_args.args[0]
        self.assertTrue(persisted["gsv_auto_start"])
        apply_runtime.assert_called_once_with(persisted, defaults_module.AI_DEFAULT_VALUES)
        self.assertTrue(self.panel._gsv_auto_start.isChecked())
        service.reload_voice_package.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
