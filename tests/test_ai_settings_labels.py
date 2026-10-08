"""AI 设置面板的文案/名称表：从 `ai_settings_panel.py` 拆出的后端中立纯数据与纯函数。

这族逻辑不碰 Qt、不碰 UI 装配、不读 config。测试覆盖三层：表格规模与内容、
查询函数与"键不存在时的兜底"、以及模块本身能在阻断 PyQt5 的进程里导入。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.script.ui import ai_settings_labels as labels


_REPO_ROOT = Path(__file__).resolve().parents[1]


class LabelTableTests(unittest.TestCase):
    def test_section_help_text_lookup_returns_registered_text(self):
        self.assertTrue(labels.SECTION_HELP_TEXTS)
        for (category_id, dict_name), text in labels.SECTION_HELP_TEXTS.items():
            self.assertEqual(labels.section_help_text(category_id, dict_name), text)

    def test_section_help_text_returns_empty_for_unregistered_pair(self):
        self.assertEqual(labels.section_help_text("no-such-category", "NOPE"), "")
        self.assertEqual(labels.section_help_text("", ""), "")

    def test_section_help_text_coerces_keys_to_str(self):
        (category_id, dict_name), text = next(iter(labels.SECTION_HELP_TEXTS.items()))
        # 传入同值但非 str 的键仍应命中（实现用 str(...) 归一）。
        class _Str(str):
            pass

        self.assertEqual(labels.section_help_text(_Str(category_id), _Str(dict_name)), text)


class FriendlyNameTests(unittest.TestCase):
    def test_known_dict_names_are_translated(self):
        self.assertEqual(labels.friendly_section_name("UI"), labels.DICT_FRIENDLY_NAME["UI"])

    def test_unknown_dict_falls_back_to_argument_or_key(self):
        self.assertEqual(labels.friendly_section_name("NOT_A_DICT"), "NOT_A_DICT")
        self.assertEqual(labels.friendly_section_name("NOT_A_DICT", "X"), "X")

    def test_explicit_fallback_that_differs_from_key_wins(self):
        self.assertEqual(labels.friendly_section_name("UI", "自定义标题"), "自定义标题")

    def test_field_section_name_special_cases_wuwa(self):
        self.assertEqual(
            labels.friendly_field_section_name("CLOUD_MUSIC", "launch_wuwa_path"),
            "鸣潮设置",
        )
        self.assertEqual(labels.friendly_field_section_name("UI", "x"), labels.friendly_section_name("UI", "UI"))

    def test_key_name_translates_and_falls_back(self):
        # 任取一个已登记的键，翻译结果与其登记值一致。
        dict_name, table = next(iter(labels.KEY_FRIENDLY_NAME.items()))
        key = next(iter(table))
        self.assertEqual(labels.friendly_key_name(dict_name, key), table[key])
        self.assertEqual(labels.friendly_key_name(dict_name, "no-such-key"), "no-such-key")
        self.assertEqual(labels.friendly_key_name("NOT_A_DICT", "k"), "k")

    def test_animation_folder_display_name_strips_suffix(self):
        self.assertEqual(labels.animation_folder_display_name("idle_anima"), "idle")
        self.assertEqual(labels.animation_folder_display_name("plain"), "plain")
        self.assertEqual(labels.animation_folder_display_name(""), "")
        self.assertEqual(labels.animation_folder_display_name(None), "")


class LabelModuleIsBackendNeutralTests(unittest.TestCase):
    def test_module_does_not_import_qt(self):
        import ast

        source = Path(labels.__file__).read_text(encoding="utf-8")
        names = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        self.assertFalse([name for name in names if "PyQt5" in name])

    def test_imports_with_pyqt_blocked(self):
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.script.ui import ai_settings_labels as L

            assert L.section_help_text("no-such", "NOPE") == ""
            assert L.friendly_key_name("NOT_A_DICT", "k") == "k"
            assert L.animation_folder_display_name("x_anima") == "x"
            print("OK")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
