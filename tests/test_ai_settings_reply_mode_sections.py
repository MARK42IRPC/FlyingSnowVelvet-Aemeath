import os
import unittest
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
from lib.script.workbench.theme import COLORS as WORKBENCH_COLORS, workbench_stylesheet


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
        with patch.object(AISettingsPanel, "_refresh_hardware_watermark_async", lambda self: None):
            self.panel = AISettingsPanel(lazy_workbench_pages=True)

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self._voice_package_probe.stop()

    def _select_mode(self, mode: str) -> None:
        self.panel._force_mode.setCurrentIndex(self.panel._force_mode.findData(mode))
        self.app.processEvents()

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
            ("元宝", "4"),
        ])

    def test_mode_specific_sections_are_hidden_until_selected(self):
        self._select_mode("1")
        self.assertFalse(self.panel._welfare_section.isHidden())
        self.assertTrue(self.panel._manual_api_section.isHidden())
        self.assertTrue(self.panel._ollama_section.isHidden())
        self.assertTrue(self.panel._yuanbao_section.isHidden())

        self._select_mode("0")
        self.assertTrue(self.panel._welfare_section.isHidden())
        self.assertFalse(self.panel._manual_api_section.isHidden())
        self.assertTrue(self.panel._ollama_section.isHidden())
        self.assertTrue(self.panel._yuanbao_section.isHidden())

        self._select_mode("2")
        self.assertTrue(self.panel._welfare_section.isHidden())
        self.assertTrue(self.panel._manual_api_section.isHidden())
        self.assertFalse(self.panel._ollama_section.isHidden())
        self.assertTrue(self.panel._yuanbao_section.isHidden())

        self._select_mode("4")
        self.assertTrue(self.panel._welfare_section.isHidden())
        self.assertTrue(self.panel._manual_api_section.isHidden())
        self.assertTrue(self.panel._ollama_section.isHidden())
        self.assertFalse(self.panel._yuanbao_section.isHidden())

    def test_yuanbao_login_actions_are_mutually_exclusive(self):
        self.panel._set_yuanbao_login_actions(logged_in=False)
        self.assertFalse(self.panel._start_yuanbao_wechat_login_btn.isHidden())
        self.assertTrue(self.panel._stop_yuanbao_login_btn.isHidden())

        self.panel._on_yuanbao_login_status_event(
            panel_module.Event(
                panel_module.EventType.YUANBAO_LOGIN_QR_STATUS,
                {"logged_in": True},
            )
        )

        self.assertTrue(self.panel._start_yuanbao_wechat_login_btn.isHidden())
        self.assertFalse(self.panel._stop_yuanbao_login_btn.isHidden())

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
        self.assertIn(f"background: {WORKBENCH_COLORS.pink}", stylesheet)

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
        self.panel._collect_values = Mock(return_value=ai_values)
        self.panel._collect_all_general_config_values = Mock(return_value=general_values)
        self.panel._apply_all_external_config_fields = Mock()
        self.panel._emit_info = Mock()

        with patch.object(panel_module, "save_ai_values") as save_ai, patch.object(
            panel_module, "_save_general_config"
        ) as save_general, patch.object(panel_module, "apply_ai_runtime") as apply_ai, patch.object(
            panel_module, "_apply_general_runtime"
        ) as apply_general:
            saved = self.panel._on_save(apply_runtime=False)

        self.assertTrue(saved)
        save_ai.assert_called_once()
        save_general.assert_called_once_with(general_values)
        apply_ai.assert_not_called()
        apply_general.assert_not_called()

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

        voice_section = Mock()
        panel = type("GsvPanel", (), {
            "_gsv_launcher_available": True,
            "_voice_section": voice_section,
        })()
        AISettingsPanel._update_gsv_settings_visibility(panel)

        voice_section.setVisible.assert_called_once_with(True)

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
