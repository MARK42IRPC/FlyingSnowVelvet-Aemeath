"""贡献名单解析：从 `ai_settings_panel.py` 拆出的后端中立纯逻辑。

本模块不碰 Qt、不碰 UI 装配。测试分两层：纯函数行为（解析 / 名称归一 / 兜底判定），
以及"能在阻断 PyQt5 的进程里导入并跑通"——拆出它的目的之一就是让这段逻辑不再被
面板的 Qt 面拖住。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.script.ui import ai_settings_contributions as contributions


_REPO_ROOT = Path(__file__).resolve().parents[1]


class ContributionParsingTests(unittest.TestCase):
    def test_role_line_and_url_entries_become_records(self):
        text = "\n".join(
            (
                "贡献:开发者-Mark42的铁镐（Mark42IRPC）-保留所有权利",
                "===https://space.bilibili.com/486401719",
                "贡献:配音-猫咪",
                "===https://space.bilibili.com/1838261330",
            )
        )
        records = contributions.parse_contribution_records(text)
        self.assertEqual(
            records,
            [
                {
                    "name": "Mark42的铁镐（Mark42IRPC）",
                    "role": "开发者",
                    "url": "https://space.bilibili.com/486401719",
                },
                {
                    "name": "猫咪",
                    "role": "配音",
                    "url": "https://space.bilibili.com/1838261330",
                },
            ],
        )

    def test_short_bare_line_becomes_fallback_name_on_flush(self):
        # 角色行没有 URL 条目时，flush 用兜底名字成条（url 为空）。
        text = "\n".join(
            (
                "贡献:测试-A",
                "===某位贡献者",
            )
        )
        records = contributions.parse_contribution_records(text)
        self.assertEqual(
            records,
            [{"name": "某位贡献者", "role": "测试", "url": ""}],
        )

    def test_url_entry_prefers_role_default_name_over_fallback(self):
        # 有 URL 时名字取角色行默认名，兜底名字不覆盖它。
        text = "\n".join(
            (
                "贡献:测试-A",
                "===某位贡献者",
                "===https://example.com/x",
            )
        )
        records = contributions.parse_contribution_records(text)
        self.assertEqual(records[0]["name"], "A")
        self.assertEqual(records[0]["url"], "https://example.com/x")

    def test_blocked_token_line_is_not_a_fallback_name(self):
        text = "\n".join(
            (
                "贡献:测试-A",
                "===感谢大家的支持",
                "===https://example.com/y",
            )
        )
        records = contributions.parse_contribution_records(text)
        # 「感谢」触发兜底屏蔽，名字退回角色行的默认名 A。
        self.assertEqual(records[0]["name"], "A")

    def test_header_without_dash_keeps_single_name(self):
        self.assertEqual(contributions.split_contribution_header("只有一个人"), ("只有一个人", ""))

    def test_ignored_title_parts_stay_out_of_the_role(self):
        role, name = contributions.split_contribution_header("开发者-保留所有权利-某人")
        self.assertEqual(name, "某人")
        self.assertNotIn("保留所有权利", role)

    def test_normalize_collapses_whitespace_and_strips_separators(self):
        self.assertEqual(contributions.normalize_contribution_name("  a   b -=:：  "), "a b")

    def test_extract_first_url_returns_empty_without_url(self):
        self.assertEqual(contributions.extract_first_url("没有链接"), "")
        self.assertEqual(
            contributions.extract_first_url("前缀 https://a.b/c 后缀"),
            "https://a.b/c",
        )


class ContributionRecordsAgainstRealFileTests(unittest.TestCase):
    def test_real_document_parses_and_manual_entries_are_inserted(self):
        path = contributions.contribution_list_path()
        self.assertTrue(path.is_file(), f"缺失贡献名单事实源：{path}")
        records = contributions.load_contribution_records()
        urls = [record["url"] for record in records]
        # 手工条目按 URL 去重后仍在结果里。
        for manual in contributions._MANUAL_CONTRIBUTION_RECORDS:
            self.assertIn(manual["url"], urls)
        # 隐藏角色不出现在结果里。
        self.assertNotIn("安装教程指引", {record["role"] for record in records})

    def test_root_argument_overrides_document_root(self):
        custom = contributions.contribution_list_path(Path("/tmp/does-not-exist-root"))
        self.assertEqual(
            custom,
            Path("/tmp/does-not-exist-root") / "doc" / "贡献名单和主播的狗盆" / "开发贡献.txt",
        )


class ContributionModuleIsBackendNeutralTests(unittest.TestCase):
    def test_module_does_not_import_qt(self):
        import ast

        source = Path(contributions.__file__).read_text(encoding="utf-8")
        names = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        self.assertFalse([name for name in names if "PyQt5" in name])

    def test_imports_and_parses_with_pyqt_blocked(self):
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.script.ui import ai_settings_contributions as c

            records = c.parse_contribution_records(
                "贡献:测试-A\\n===https://example.com/z"
            )
            assert records == [
                {"name": "A", "role": "测试", "url": "https://example.com/z"}
            ], records
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
