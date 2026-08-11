from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config.ollama_config as ollama_config
from config.config import BUBBLE_CONFIG
from lib.script.chat.handler_persona import ChatHandlerPersonaMixin
from lib.script.chat.persona_storage import (
    ensure_user_persona_file,
    resolve_persona_file_path,
)
from lib.script.ui import ai_settings_panel as ai_settings_module


class PersonaStorageTests(unittest.TestCase):
    def test_user_persona_has_runtime_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"AEMEATH_DESK_PET_HOME": tmpdir},
            clear=False,
        ):
            user_persona = Path(tmpdir) / "user" / "persona.txt"
            user_persona.parent.mkdir(parents=True)
            user_persona.write_text("用户人格", encoding="utf-8")

            self.assertEqual(resolve_persona_file_path(), user_persona)
            loaded = ChatHandlerPersonaMixin._load_persona(SimpleNamespace())
            self.assertEqual(loaded, "用户人格")

    def test_first_edit_copies_default_once_without_later_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"AEMEATH_DESK_PET_HOME": tmpdir},
            clear=False,
        ):
            default_persona = Path(tmpdir) / "default-persona.txt"
            default_persona.write_text("默认人格", encoding="utf-8")
            with patch.dict(
                BUBBLE_CONFIG,
                {"default_persona_file": str(default_persona)},
                clear=False,
            ), patch.object(ollama_config, "PERSONA_FILE", ""):
                user_persona = ensure_user_persona_file()
                self.assertEqual(user_persona.read_text(encoding="utf-8"), "默认人格")

                user_persona.write_text("用户修改", encoding="utf-8")
                default_persona.write_text("更新后的默认人格", encoding="utf-8")
                self.assertEqual(ensure_user_persona_file(), user_persona)
                self.assertEqual(user_persona.read_text(encoding="utf-8"), "用户修改")

    def test_first_edit_migrates_legacy_custom_persona_before_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"AEMEATH_DESK_PET_HOME": tmpdir},
            clear=False,
        ):
            custom_persona = Path(tmpdir) / "custom.txt"
            default_persona = Path(tmpdir) / "default.txt"
            custom_persona.write_text("旧自定义人格", encoding="utf-8")
            default_persona.write_text("默认人格", encoding="utf-8")
            with patch.object(
                ollama_config,
                "PERSONA_FILE",
                str(custom_persona),
            ), patch.dict(
                BUBBLE_CONFIG,
                {"default_persona_file": str(default_persona)},
                clear=False,
            ):
                user_persona = ensure_user_persona_file()

            self.assertEqual(user_persona.read_text(encoding="utf-8"), "旧自定义人格")

    def test_settings_action_opens_initialized_user_persona(self) -> None:
        user_persona = Path("C:/AemeathDeskPet/user/persona.txt")
        panel = SimpleNamespace(
            _open_path_with_system_default=Mock(),
            _emit_info=Mock(),
        )
        with patch.object(
            ai_settings_module,
            "ensure_user_persona_file",
            return_value=user_persona,
        ):
            ai_settings_module.AISettingsPanel._on_open_persona_file(panel)

        panel._open_path_with_system_default.assert_called_once_with(user_persona)
        panel._emit_info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
