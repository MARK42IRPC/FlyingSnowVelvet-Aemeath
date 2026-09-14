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

from PyQt5.QtWidgets import QApplication, QComboBox

from lib.core.event.center import EventType
from lib.script.ui import ai_settings_panel as panel_module
from lib.script.ui.ai_settings_panel import AISettingsPanel
from lib.script.gsvmove.package_manager import VoicePackageStatus
from lib.script.workbench.theme import get_workbench_colors, workbench_stylesheet


class AISettingsReplyModeSectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._voice_package_probe = patch.object(
            panel_module,
            "get_voice_package_status",
            return_value=VoicePackageStatus("missing", "not installed"),
        )
        self._voice_package_probe.start()
        self._local_dsh_probe = patch.object(
            AISettingsPanel,
            "_probe_local_dsh",
            return_value={"available": False, "reason": "未探测到 本机 DeepSeek Harness"},
        )
        self._local_dsh_probe.start()
        with patch.object(AISettingsPanel, "_refresh_hardware_watermark_async", lambda self: None):
            self.panel = AISettingsPanel(lazy_workbench_pages=True)

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self._local_dsh_probe.stop()
        self._voice_package_probe.stop()

    def _select_mode(self, mode: str) -> None:
        self.panel._force_mode.setCurrentIndex(self.panel._force_mode.findData(mode))
        self.app.processEvents()

    def _build_panel(self, status: dict, *, saved_backend: str | None = None):
        """按给定探测结果重建面板（本机 DSH 探测只在构造期读一次）。"""
        probe = patch.object(AISettingsPanel, "_probe_local_dsh", return_value=dict(status))
        probe.start()
        self.addCleanup(probe.stop)
        with patch.object(AISettingsPanel, "_refresh_hardware_watermark_async", lambda self: None):
            panel = AISettingsPanel(lazy_workbench_pages=True)
        self.addCleanup(panel.deleteLater)
        if saved_backend is not None:
            panel._set_values_to_form({"office_backend": saved_backend})
        return panel

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

    def test_office_backend_hides_local_entry_when_probe_finds_nothing(self):
        field = self.panel._office_backend

        self.assertEqual(self._backend_items(field), [("DeepSeek Harness（推荐）", "dsh")])
        self.assertEqual(field.currentData(), "dsh")
        self.assertIn("DeepSeek Harness", self.panel._office_backend_description())

    def test_office_backend_lists_local_entry_when_probe_succeeds(self):
        panel = self._build_panel({
            "available": True,
            "version": "0.1.0-rc.6",
            "source": "npm 全局目录",
            "path": r"C:\Users\demo\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh",
        })
        field = panel._office_backend

        self.assertEqual(self._backend_items(field), [
            ("DeepSeek Harness（推荐）", "dsh"),
            ("本机 DeepSeek Harness", "local_dsh"),
        ])
        self.assertTrue(field.model().item(field.findData("local_dsh")).isEnabled())

        field.setCurrentIndex(field.findData("local_dsh"))

        description = panel._office_backend_description()
        self.assertIn("0.1.0-rc.6", description)
        self.assertIn("npm 全局目录", description)
        self.assertIn(r"AppData\Roaming\npm", description)

    def test_office_backend_keeps_saved_local_choice_when_probe_finds_nothing(self):
        panel = self._build_panel(
            {"available": False, "reason": "未探测到 本机 DeepSeek Harness"},
            saved_backend="local_dsh",
        )
        field = panel._office_backend

        self.assertEqual(self._backend_items(field), [
            ("DeepSeek Harness（推荐）", "dsh"),
            ("本机 DeepSeek Harness（未探测到）", "local_dsh"),
        ])
        self.assertFalse(field.model().item(field.findData("local_dsh")).isEnabled())
        self.assertEqual(field.currentData(), "local_dsh")
        self.assertIn("未探测到", panel._office_backend_description())

    def test_office_backend_does_not_duplicate_local_entry_on_reload(self):
        panel = self._build_panel({"available": True, "version": "0.1.0-rc.6"})

        panel._set_values_to_form({"office_backend": "local_dsh"})
        panel._set_values_to_form({"office_backend": "dsh"})

        field = panel._office_backend
        entries = [data for _text, data in self._backend_items(field)]
        self.assertEqual(entries.count("local_dsh"), 1)
        self.assertEqual(field.currentData(), "dsh")

    def test_office_backend_does_not_hide_independent_api_toggle_or_warmup(self):
        self.assertFalse(self.panel._office_backend.isHidden())
        self.assertFalse(self.panel._office_use_independent_api.isHidden())
        self.assertFalse(self.panel._office_warmup_on_startup.isHidden())
        self.assertTrue(self.panel._office_independent_api_group.isHidden())
        self.assertEqual(
            self.panel._office_independent_api_form.rowCount(),
            len(self.panel._office_independent_api_rows),
        )

        self.panel._office_use_independent_api.setChecked(True)

        self.assertFalse(self.panel._office_backend.isHidden())
        self.assertFalse(self.panel._office_use_independent_api.isHidden())
        self.assertFalse(self.panel._office_warmup_on_startup.isHidden())
        self.assertFalse(self.panel._office_independent_api_group.isHidden())
        for field in self.panel._office_independent_api_rows:
            self.assertFalse(field.isHidden())

    def test_collapsed_office_group_releases_its_row_space(self):
        section = self.panel._office_mode_section
        group = self.panel._office_independent_api_group

        self.panel._office_use_independent_api.setChecked(True)
        expanded = self._body_size_hint(section)
        self.panel._office_use_independent_api.setChecked(False)
        collapsed = self._body_size_hint(section)

        self.assertAlmostEqual(
            expanded - collapsed,
            group.sizeHint().height() + section.body_layout.spacing(),
            delta=2,
        )

    def test_auto_companion_interval_slider_uses_minute_limits(self):
        field = self.panel._auto_companion_interval_minutes

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

        with patch.object(panel_module, "save_ai_values") as save_ai, patch.object(
            panel_module, "_save_general_config"
        ) as save_general, patch.object(panel_module, "apply_ai_runtime") as apply_ai, patch.object(
            panel_module, "_apply_general_runtime"
        ) as apply_general, patch.object(panel_module, "get_compute_hub", return_value=hub):
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

        with patch.object(panel_module, "get_compute_hub", return_value=hub):
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

        with patch.object(panel_module, "get_compute_hub", return_value=hub):
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
        with patch.object(panel_module.requests, "get", return_value=response) as request:
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

    def test_nvidia_acceleration_switch_follows_driver_presence(self):
        voice_section = Mock()
        checkbox = Mock()
        panel = type("GsvPanel", (), {
            "_gsv_launcher_available": True,
            "_voice_section": voice_section,
            "_gsv_nvidia_cuda_acceleration": checkbox,
            "_nvidia_gpu_present": True,
        })()

        AISettingsPanel._update_gsv_settings_visibility(panel)
        checkbox.setVisible.assert_called_once_with(True)

        checkbox.reset_mock()
        panel._nvidia_gpu_present = False
        AISettingsPanel._update_gsv_settings_visibility(panel)
        checkbox.setVisible.assert_called_once_with(False)

        checkbox.reset_mock()
        panel._gsv_launcher_available = False
        panel._nvidia_gpu_present = True
        AISettingsPanel._update_gsv_settings_visibility(panel)
        checkbox.setVisible.assert_called_once_with(False)

    def test_nvidia_switch_has_no_installer_widget(self):
        self.assertFalse(hasattr(self.panel, "_install_cuda_runtime_button"))
        self.assertEqual(self.panel._gsv_nvidia_cuda_acceleration.text(), "N卡加速")

    def test_acceleration_capability_check_runs_only_after_settings_requests_it(self):
        class ImmediateHub:
            @staticmethod
            def submit_interactive_io(func):
                func()
                return object()

        self.panel._gsv_launcher_available = True
        self.panel._nvidia_gpu_present = False
        with patch.object(panel_module, "has_nvidia_gpu", return_value=True), patch.object(
            panel_module, "get_compute_hub", return_value=ImmediateHub()
        ):
            self.panel._refresh_nvidia_acceleration_capability_async()
            self.app.processEvents()

        self.assertFalse(self.panel._cuda_capability_pending)
        self.assertTrue(self.panel._nvidia_gpu_present)
        self.assertFalse(self.panel._gsv_nvidia_cuda_acceleration.isHidden())

    def test_acceleration_capability_check_recovers_when_probe_raises(self):
        class ImmediateHub:
            @staticmethod
            def submit_interactive_io(func):
                func()
                return object()

        self.panel._gsv_launcher_available = True
        self.panel._nvidia_gpu_present = True
        with patch.object(
            panel_module, "has_nvidia_gpu", side_effect=RuntimeError("probe failed")
        ), patch.object(
            panel_module, "get_compute_hub", return_value=ImmediateHub()
        ):
            self.panel._refresh_nvidia_acceleration_capability_async()
            self.app.processEvents()

        self.assertFalse(self.panel._cuda_capability_pending)
        self.assertFalse(self.panel._nvidia_gpu_present)
        self.assertTrue(self.panel._gsv_nvidia_cuda_acceleration.isHidden())

    def test_voice_package_install_enables_and_persists_runtime(self):
        saved_values = dict(panel_module._DEFAULT_VALUES)
        saved_values["gsv_auto_start"] = False
        service = Mock()
        self.panel._gsv_auto_start.setChecked(False)

        with patch.object(
            panel_module, "load_ai_values", return_value=saved_values
        ), patch.object(panel_module, "save_ai_values") as save_values, patch.object(
            panel_module, "apply_ai_runtime"
        ) as apply_runtime, patch.object(
            panel_module,
            "get_voice_package_status",
            return_value=VoicePackageStatus("installed", "ok"),
        ), patch(
            "lib.script.gsvmove.get_gsvmove_service", return_value=service
        ):
            self.panel._on_voice_package_installed()

        persisted = save_values.call_args.args[0]
        self.assertTrue(persisted["gsv_auto_start"])
        apply_runtime.assert_called_once_with(persisted, panel_module._DEFAULT_VALUES)
        self.assertTrue(self.panel._gsv_auto_start.isChecked())
        service.reload_voice_package.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
