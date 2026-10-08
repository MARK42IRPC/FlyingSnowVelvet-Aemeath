"""AI 设置面板的配置 schema 与取值格式化：从 `ai_settings_panel.py` 拆出的后端中立纯逻辑。

测试只钉住拆出后的**形状与不变量**（不复制整张表），重点在"面板与新模块读同一份数据"
以及"模块可在阻断 PyQt5 的进程里导入"。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.script.ui import ai_settings_config_schema as schema


_REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaTests(unittest.TestCase):
    def test_tables_are_non_empty(self):
        self.assertTrue(schema.CATEGORY_KEY_ALLOWLIST)
        self.assertTrue(schema.GENERAL_CONFIG_DEFAULTS)
        self.assertTrue(schema.GENERAL_NUMERIC_RULES)
        self.assertTrue(schema.GENERAL_CHOICE_FIELD_OPTIONS)

    def test_category_allowlist_maps_category_to_dict_to_key_sets(self):
        for category_id, mapping in schema.CATEGORY_KEY_ALLOWLIST.items():
            self.assertIsInstance(category_id, str)
            self.assertIsInstance(mapping, dict)
            for dict_name, keys in mapping.items():
                self.assertIsInstance(dict_name, str)
                # 空集合合法：存在"声明了字典但当前没有可编辑键"的分类。
                self.assertIsInstance(keys, (set, frozenset))

    def test_numeric_rules_are_well_formed(self):
        for (dict_name, key), spec in schema.GENERAL_NUMERIC_RULES.items():
            self.assertIsInstance(dict_name, str)
            self.assertIsInstance(key, str)
            kind, low, high = spec
            self.assertLessEqual(low, high, f"{dict_name}.{key} 的区间下界不得高于上界")

    def test_range_relations_have_min_max_signatures(self):
        # 每组是 (dict_name, left_key, right_key)；左键的签名角色应是 min、右键是 max。
        for dict_name, left_key, right_key in schema.GENERAL_RANGE_RELATIONS:
            left = schema.range_pair_signature(left_key)
            right = schema.range_pair_signature(right_key)
            self.assertIsNotNone(left, f"{dict_name}.{left_key} 应能解析出区间签名")
            self.assertIsNotNone(right, f"{dict_name}.{right_key} 应能解析出区间签名")
            self.assertEqual(left[0], right[0], "同一组左右键应归一到同一个区间名")
            self.assertEqual(left[1], "min")
            self.assertEqual(right[1], "max")

    def test_range_pair_signature_returns_none_for_non_range_keys(self):
        self.assertIsNone(schema.range_pair_signature("plain_key"))
        self.assertEqual(schema.range_pair_signature("x_min"), ("x", "min"))
        self.assertEqual(schema.range_pair_signature("x_max"), ("x", "max"))


class ConfigValueFormattingTests(unittest.TestCase):
    def test_format_config_editor_value_uses_repr_for_non_strings(self):
        # 字符串原样返回；其它值走 repr（None -> "None"，True -> "True"）。
        self.assertEqual(schema.format_config_editor_value("x"), "x")
        self.assertEqual(schema.format_config_editor_value(None), "None")
        self.assertEqual(schema.format_config_editor_value(True), "True")
        self.assertEqual(schema.format_config_editor_value(3), "3")
        self.assertEqual(schema.format_config_editor_value(1.5), "1.5")
        self.assertEqual(schema.format_config_editor_value([1, 2]), "[1, 2]")

    def test_is_supported_config_value_accepts_scalars_and_flat_sequences(self):
        for value in (True, 1, 1.5, "s", [1, 2], (1, 2), []):
            self.assertTrue(schema.is_supported_config_value(value), value)

    def test_is_supported_config_value_rejects_none_and_dicts_and_nested(self):
        for value in (None, {"a": 1}, [[1]], (1, [2])):
            self.assertFalse(schema.is_supported_config_value(value), value)

    def test_hardcoded_default_returns_registered_or_fallback(self):
        self.assertEqual(schema.hardcoded_general_default("NOPE", "nope", "FB"), "FB")
        # 找一个已登记项，验证取到的是登记值而不是兜底。
        dict_name = next(iter(schema.GENERAL_CONFIG_DEFAULTS))
        key = next(iter(schema.GENERAL_CONFIG_DEFAULTS[dict_name]))
        sentinel = object()
        self.assertIsNot(schema.hardcoded_general_default(dict_name, key, sentinel), sentinel)


class SchemaModuleIsBackendNeutralTests(unittest.TestCase):
    def test_module_does_not_import_qt(self):
        import ast

        source = Path(schema.__file__).read_text(encoding="utf-8")
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

            from lib.script.ui import ai_settings_config_schema as s

            assert s.format_config_editor_value(None) == "None"
            assert s.CATEGORY_KEY_ALLOWLIST
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
