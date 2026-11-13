"""论坛卡片正文富文本控件下沉档位 D（第 75 轮收敛）。

`lib/script/ui/forum_text.py` 原先整个文件就是纯 `QTextEdit` 子类 `MarkupText` 加四个描边常量与
`bold_outline_width()`：它不 import 任何产品包，做的是「把 `forum_markup` 的片段排到
`QTextDocument` 上」这件控件工具包事实，按《render 层边界契约》第 3 节的档位 D 判定下沉到
`lib/core/render/backends/qt/widgets/forum_text.py`。

本模块钉住：

- 宿主不 import `lib.script`，产品垫片不含 `PyQt5`；
- 四个常量、`bold_outline_width()` 与 `MarkupText` 仍解析到宿主（调用方导入面不变）；
- 行为契约不变：留言墙（不传 `runs`）走「整篇一色、只给加粗片段加同色描边」；
  详情页（传 `runs`）按段着色、按段描边，整篇描边时粗体笔宽加倍。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT = _REPO_ROOT / "lib" / "script" / "ui" / "forum_text.py"
_HOST = (
    _REPO_ROOT
    / "lib"
    / "core"
    / "render"
    / "backends"
    / "qt"
    / "widgets"
    / "forum_text.py"
)


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _fragments(widget):
    """正文文档里每个可见片段的 `(文字, 字符格式)`，按排版顺序。"""
    pieces = []
    block = widget.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.text():
                pieces.append((fragment.text(), fragment.charFormat()))
            it += 1
        block = block.next()
    return pieces


class ForumTextHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _markup(self, text, **kwargs):
        from lib.core.render.backends.qt.widgets.forum_text import MarkupText
        from lib.script.ui import render_bridge
        from lib.script.ui.forum_markup import to_html
        from lib.script.ui.forum_style import forum_card_text_color

        options = dict(
            font=render_bridge.ui_font(size=17),
            color=forum_card_text_color() or "#101820",
            width_hint=430,
        )
        options.update(kwargs)
        widget = MarkupText(to_html(text), **options)
        self.addCleanup(widget.deleteLater)
        return widget

    def test_host_never_imports_product_modules(self):
        self.assertEqual(
            [n for n in _imported_names(_HOST) if n == "lib.script" or n.startswith("lib.script.")],
            [],
        )

    def test_product_shim_is_not_a_qt_implementation(self):
        names = _imported_names(_PRODUCT)
        self.assertEqual([n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [])
        self.assertNotIn("QtWidgets", _PRODUCT.read_text(encoding="utf-8"))

    def test_product_names_resolve_to_the_host_definitions(self):
        from lib.core.render.backends.qt.widgets import forum_text as host
        from lib.script.ui import forum_text as product

        for name in (
            "BOLD_OUTLINE_RATIO",
            "BOLD_OUTLINE_MIN_PX",
            "BOLD_OUTLINE_MAX_PX",
            "MESSAGE_OUTLINE_BOLD_GAIN",
        ):
            with self.subTest(name=name):
                self.assertEqual(getattr(product, name), getattr(host, name))
        self.assertIs(product.MarkupText, host.MarkupText)
        self.assertIs(product.bold_outline_width, host.bold_outline_width)

    def test_bold_outline_width_is_bounded_and_monotonic(self):
        from lib.script.ui.forum_text import (
            BOLD_OUTLINE_MAX_PX,
            BOLD_OUTLINE_MIN_PX,
            bold_outline_width,
        )

        self.assertGreater(BOLD_OUTLINE_MIN_PX, 0)
        self.assertEqual(bold_outline_width(8), BOLD_OUTLINE_MIN_PX)
        self.assertEqual(bold_outline_width(96), BOLD_OUTLINE_MAX_PX)
        widths = [bold_outline_width(size) for size in (10, 12, 17, 24, 34, 48)]
        self.assertEqual(widths, sorted(widths))

    def test_wall_path_colours_one_run_and_only_strokes_bold(self):
        from PyQt5.QtCore import Qt

        from lib.script.ui.forum_text import bold_outline_width

        widget = self._markup("**雪绒**最棒")
        pieces = dict(_fragments(widget))
        self.assertEqual(set(pieces), {"雪绒", "最棒"})
        bold = pieces["雪绒"]
        plain = pieces["最棒"]
        self.assertNotEqual(bold.textOutline().style(), Qt.NoPen)
        self.assertAlmostEqual(
            bold.textOutline().widthF(),
            bold_outline_width(widget.font().pixelSize()),
            delta=0.01,
        )
        # 留言墙那条路径：没有 `[outline]` 令牌，普通字一律不描边。
        self.assertEqual(plain.textOutline().style(), Qt.NoPen)

    def test_outline_all_strokes_whole_message_and_keeps_bold_heavier(self):
        from lib.script.ui.forum_text import (
            MESSAGE_OUTLINE_BOLD_GAIN,
            bold_outline_width,
        )

        widget = self._markup(
            "**加粗**和普通", outline_color="#ff3b30", outline_all=True
        )
        pieces = dict(_fragments(widget))
        base = bold_outline_width(widget.font().pixelSize())
        self.assertAlmostEqual(pieces["和普通"].textOutline().widthF(), base, delta=0.01)
        self.assertEqual(pieces["和普通"].textOutline().color().name(), "#ff3b30")
        self.assertAlmostEqual(
            pieces["加粗"].textOutline().widthF(),
            base * MESSAGE_OUTLINE_BOLD_GAIN,
            delta=0.01,
        )

    def test_detail_runs_colour_and_stroke_per_paragraph(self):
        from lib.core.forum_colors import ForumTextRun

        widget = self._markup(
            "红字和绿描边",
            runs=(
                ForumTextRun("红字和", color="#1f6feb"),
                ForumTextRun("绿描边", outline="#00ff00"),
            ),
        )
        pieces = dict(_fragments(widget))
        self.assertEqual(pieces["红字和"].foreground().color().name(), "#1f6feb")
        self.assertEqual(pieces["绿描边"].textOutline().color().name(), "#00ff00")


if __name__ == "__main__":
    unittest.main()
