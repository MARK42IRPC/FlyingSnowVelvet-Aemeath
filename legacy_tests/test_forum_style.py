"""论坛样式：底纹混色与 accent 取色的纯十六进制实现（后端中立）。"""

from __future__ import annotations

import unittest

from lib.script.ui import forum_style


class ForumTextureColorTests(unittest.TestCase):
    def test_blend_matches_the_frozen_table(self):
        # 收敛自 QColor 的整数混色；表里含合法/非法 accent 与三种模式。
        table = {
            ("dark", "pink"): "#ffd5e4",
            ("dark", "cyan"): "#d1edff",
            ("dark", "blue"): "#d8e1ff",
            ("dark", "snow"): "#f2f5fa",
            ("dark", ""): "#ffffff",
            ("dark", "bogus"): "#ffd5e4",
            ("dark", None): "#ffffff",
            ("light", "pink"): "#5a2a3e",
            ("light", "cyan"): "#1e4056",
            ("light", "blue"): "#242e50",
            ("light", "snow"): "#393e46",
            ("light", ""): "#000000",
            ("light", "bogus"): "#5d2a3f",
            ("light", None): "#000000",
        }
        for (mode, accent), expected in table.items():
            with self.subTest(mode=mode, accent=accent):
                self.assertEqual(forum_style.forum_texture_color(mode, accent), expected)

    def test_unknown_mode_behaves_as_dark(self):
        self.assertEqual(
            forum_style.forum_texture_color("nonsense", "pink"),
            forum_style.forum_texture_color("dark", "pink"),
        )

    def test_module_no_longer_imports_qt(self):
        import ast
        from pathlib import Path

        source = Path(forum_style.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        self.assertFalse([name for name in names if "PyQt5" in name])


if __name__ == "__main__":
    unittest.main()
