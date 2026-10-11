"""论坛族整族出列（第 76 轮，批次 1）。

`lib/script/ui/forum_*` 十二个文件按《render 层边界契约》第 3 节档位归类后一次性出列：

- 纯逻辑（无 Qt、无产品包）：`forum_markup` / `forum_style` / `forum_wall_layout` → `visuals/`；
- 纯 Qt 控件族：`forum_texture` / `forum_color_picker` / `forum_color_control` / `forum_sticker`
  / `forum_body` / `forum_detail` / `forum_composer` / `forum_account_sections` / `forum_account`
  / `forum_wall` / `forum_board` / `forum_window` → `backends/qt/widgets/`。

本模块钉住族级不变量：

- 十二个产品垫片都不含 `PyQt5`，也不静态 import 档位 D（名字经 `render_bridge` 解析）；
- 档位 D 宿主不 import `lib.script`；
- 关键公开名字按原路径解析到宿主，`ForumBoardPage` 的继承面（三个 mixin）保持不变。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT = _REPO_ROOT / "lib" / "script" / "ui"
_TIER_D = _REPO_ROOT / "lib" / "core" / "render" / "backends" / "qt" / "widgets"
_VISUALS = _REPO_ROOT / "lib" / "core" / "render" / "visuals"

_PRODUCT_FILES = (
    "forum_account.py",
    "forum_account_sections.py",
    "forum_board.py",
    "forum_body.py",
    "forum_color_control.py",
    "forum_color_picker.py",
    "forum_composer.py",
    "forum_detail.py",
    "forum_markup.py",
    "forum_sticker.py",
    "forum_style.py",
    "forum_texture.py",
    "forum_wall.py",
    "forum_wall_layout.py",
    "forum_window.py",
)
_TIER_D_FILES = (
    "forum_account.py",
    "forum_account_sections.py",
    "forum_board.py",
    "forum_body.py",
    "forum_color_control.py",
    "forum_color_picker.py",
    "forum_composer.py",
    "forum_detail.py",
    "forum_sticker.py",
    "forum_texture.py",
    "forum_wall.py",
    "forum_window.py",
)
_VISUALS_FILES = ("forum_markup.py", "forum_style.py", "forum_wall_layout.py")


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class ForumFamilyTests(unittest.TestCase):
    def test_product_shims_are_not_qt_or_tier_d_implementations(self):
        for name in _PRODUCT_FILES:
            with self.subTest(module=name):
                path = _PRODUCT / name
                self.assertTrue(path.is_file(), name)
                names = _imported_names(path)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [], name
                )
                self.assertEqual(
                    [n for n in names if n.startswith("lib.core.render.backends.qt")],
                    [],
                    f"{name} 垫片不得静态 import 档位 D",
                )

    def test_tier_d_hosts_never_import_product_modules(self):
        for name in _TIER_D_FILES:
            with self.subTest(module=name):
                names = _imported_names(_TIER_D / name)
                self.assertEqual(
                    [n for n in names if n == "lib.script" or n.startswith("lib.script.")], []
                )

    def test_visuals_modules_are_backend_neutral(self):
        for name in _VISUALS_FILES:
            with self.subTest(module=name):
                names = _imported_names(_VISUALS / name)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], []
                )
                self.assertEqual(
                    [n for n in names if n == "lib.script" or n.startswith("lib.script.")], []
                )

    def test_public_names_resolve_to_the_host(self):
        from lib.core.render.backends.qt.widgets import forum_board as host_board
        from lib.core.render.backends.qt.widgets import forum_color_picker as host_picker
        from lib.core.render.backends.qt.widgets import forum_window as host_window
        from lib.core.render.visuals import forum_style as visuals_style
        from lib.script.ui import forum_board as product_board
        from lib.script.ui import forum_color_picker as product_picker
        from lib.script.ui import forum_style as product_style
        from lib.script.ui import forum_window as product_window

        self.assertIs(product_board.ForumBoardPage, host_board.ForumBoardPage)
        self.assertIs(product_board.ForumPostRow, host_board.ForumPostRow)
        self.assertIs(product_picker.ForumColorSlider, host_picker.ForumColorSlider)
        self.assertIs(product_window.ForumWindow, host_window.ForumWindow)
        self.assertIs(product_style.forum_stylesheet, visuals_style.forum_stylesheet)

    def test_board_page_keeps_its_mixin_inheritance(self):
        from lib.script.ui.forum_board import ForumBoardPage
        from lib.script.ui.forum_body import ForumBodyMixin
        from lib.script.ui.forum_composer import ForumComposerMixin
        from lib.script.ui.forum_detail import ForumDetailMixin

        for mixin in (ForumComposerMixin, ForumDetailMixin, ForumBodyMixin):
            self.assertTrue(issubclass(ForumBoardPage, mixin), mixin.__name__)

    def test_style_and_markup_facts_are_unchanged(self):
        from lib.script.ui.forum_markup import FORUM_MARKUP_FORMATS, to_html, visible_text
        from lib.script.ui.forum_style import forum_accent_color, forum_texture_color

        self.assertEqual(len(FORUM_MARKUP_FORMATS), 4)
        self.assertNotIn("*", visible_text("**雪绒**最*棒*"))
        self.assertIn("<b>", to_html("**雪绒**"))
        self.assertEqual(forum_accent_color("pink"), forum_accent_color("pink").lower())
        self.assertEqual(
            forum_texture_color("light", "pink"), forum_texture_color("light", "pink")
        )


if __name__ == "__main__":
    unittest.main()
