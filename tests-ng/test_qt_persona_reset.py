"""设置页「重置人格词」：按钮存在性、确认框门控与 hook 调用。"""

# ruff: noqa: E402 - 必须先设置 Qt 平台插件环境变量再 import PyQt5。

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QWidget

from lib.core.render.backends.qt.widgets import ai_settings_page as page_module
from lib.core.render.backends.qt.widgets.ai_settings_page_sections import (
    AISettingsPageSectionsMixin,
)
from lib.core.render.backends.qt.widgets.office_mode_settings import create_field_row_group
from lib.core.render.backends.qt.widgets.workbench_settings_layout import SettingsPageScaffold


class _InfoHolder:
    """只承接 `_on_reset_persona_file` 用到的 `_emit_info`。"""

    def __init__(self) -> None:
        self.infos: list[str] = []

    def _emit_info(self, text: str, min_tick: int = 12, max_tick: int = 140) -> None:
        del min_tick, max_tick
        self.infos.append(text)


class ResetPersonaActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cancel_does_not_touch_the_persona_file(self):
        holder = _InfoHolder()
        with patch.object(page_module, "ask_confirmation", return_value=False), patch.object(
            page_module, "reset_user_persona_file"
        ) as reset:
            page_module.AISettingsPageMixin._on_reset_persona_file(holder)

        reset.assert_not_called()
        self.assertEqual(holder.infos, [])

    def test_confirm_resets_and_reports_the_backup_name(self):
        holder = _InfoHolder()
        backup = Path("persona_backup_20260101_000000.txt")
        with patch.object(page_module, "ask_confirmation", return_value=True) as ask, patch.object(
            page_module, "reset_user_persona_file", return_value=(Path("persona.txt"), backup)
        ) as reset:
            page_module.AISettingsPageMixin._on_reset_persona_file(holder)

        reset.assert_called_once_with()
        ask.assert_called_once()
        self.assertTrue(ask.call_args.kwargs["destructive"])
        self.assertEqual(ask.call_args.kwargs["layer_name"], "ResetPersonaConfirmation")
        self.assertIn("persona_backup_20260101_000000.txt", holder.infos[-1])

    def test_reset_failure_is_reported_and_does_not_claim_success(self):
        holder = _InfoHolder()
        with patch.object(page_module, "ask_confirmation", return_value=True), patch.object(
            page_module, "reset_user_persona_file", side_effect=OSError("磁盘已满")
        ):
            page_module.AISettingsPageMixin._on_reset_persona_file(holder)

        self.assertEqual(len(holder.infos), 1)
        self.assertIn("失败", holder.infos[0])


class PersonaRowButtonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_persona_row_has_open_and_danger_reset_buttons(self):
        page = QWidget()
        scaffold = SettingsPageScaffold(page, "测试页", "说明")

        class _Holder:
            _set_form_row_description = staticmethod(lambda *args, **kwargs: None)
            _set_widget_description = staticmethod(lambda *args, **kwargs: None)
            _create_field_row_group = staticmethod(create_field_row_group)

            def __init__(self):
                self.calls = []

            def _update_auto_companion_interval_row(self):
                return None

            def _on_open_persona_file(self):
                self.calls.append("open")

            def _on_reset_persona_file(self):
                self.calls.append("reset")

        holder = _Holder()
        AISettingsPageSectionsMixin._build_reply_mode_section(holder, scaffold)

        self.assertEqual(holder._open_persona_file_btn.text(), "设置人格词")
        self.assertEqual(holder._reset_persona_file_btn.text(), "重置人格词")
        self.assertEqual(holder._reset_persona_file_btn.objectName(), "ResetPersonaButton")
        self.assertTrue(holder._reset_persona_file_btn.property("danger"))

        holder._open_persona_file_btn.click()
        holder._reset_persona_file_btn.click()
        self.assertEqual(holder.calls, ["open", "reset"])

        page.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
