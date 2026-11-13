"""设置面板整段 QSS 的事实源（`visuals/ai_settings_panel_visuals.py`）。

这段 QSS 原本长在 `ai_settings_panel._apply_style()` 里，第三轮搬到后端中立的
`visuals/`：它只读 `COLORS` / `UI_THEME` / 工作台 token 与 `scale_px`，不 import
`PyQt5`、也不 import 产品面。这里钉住三件事——调色板来源与 `qt_color_name()` 同序、
两个页面片段的内联边界（前置换行 / 末置换行）、以及模块的"无 Qt"性质。
"""

import ast
import unittest
from pathlib import Path

from config.scale import scale_px
from lib.core.render.visuals.ai_settings_panel_visuals import (
    COMBO_DROP_WIDTH,
    CONFIG_FONT_SIZE,
    DROPDOWN_ITEM_FONT_SIZE,
    ai_settings_panel_stylesheet,
    contribution_list_fragment,
    sponsor_author_fragment,
)


class AiSettingsPanelStylesheetTests(unittest.TestCase):
    def test_stylesheet_uses_the_same_palette_order_as_qt_color_name(self):
        """`qt_color_name()` 先查 `COLORS` 再查 `UI_THEME`，描述层必须同序。

        `text` 同时在两张表里（`COLORS` 是 #333333、`UI_THEME` 是 #000000）；顺序错了
        就会悄悄换掉面板正文色。
        """
        sheet = ai_settings_panel_stylesheet()

        self.assertIn("color: #333333;", sheet)
        self.assertIn("background: #ffb6c1;", sheet)     # UI_THEME.bg
        self.assertIn("background: #81c6dd;", sheet)     # UI_THEME.deep_cyan
        self.assertIn("border: 2px solid #000000;", sheet)  # UI_THEME.border

    def test_stylesheet_keeps_every_object_selector(self):
        sheet = ai_settings_panel_stylesheet()
        for selector in (
            "QLabel#ConfigSectionLabel",
            "QLabel#ConfigFormLabel",
            "QWidget#sponsorAuthorCard",
            "QPushButton#ContributionCardButton",
            "QWidget#ContributionCardAccent",
            "QLabel#ContributionCardName",
            "QLabel#ContributionCardRole",
            "QScrollArea",
            "QMenu",
            "QSlider::handle:horizontal",
        ):
            self.assertIn(selector, sheet, selector)

    def test_page_fragments_are_inline_safe(self):
        """两个片段是整段样式表的内联块：都以换行开头，末条规则以 `}}` 收尾。"""
        from lib.core.render.visuals.workbench_tokens import get_workbench_tokens

        tokens = get_workbench_tokens()
        sponsor = sponsor_author_fragment(tokens)
        contribution = contribution_list_fragment(tokens)

        self.assertTrue(sponsor.startswith("\n"))
        self.assertTrue(contribution.startswith("\n"))
        # 片段里的 `}}` 在 f-string 里转义成单个 `}`，末条规则因此以 `}` 收尾。
        self.assertTrue(sponsor.rstrip().endswith("}"))
        self.assertTrue(contribution.rstrip().endswith("}"))
        self.assertIn("QWidget#sponsorAuthorCard", sponsor)
        self.assertIn("QPushButton#ContributionCardButton", contribution)
        # 两个片段各在整段样式表里内联一次，位置与片段自身一致。
        sheet = ai_settings_panel_stylesheet()
        self.assertEqual(sheet.count("QWidget#sponsorAuthorCard"), sponsor.count("QWidget#sponsorAuthorCard"))
        self.assertEqual(
            sheet.count("QPushButton#ContributionCardButton"),
            contribution.count("QPushButton#ContributionCardButton"),
        )

    def test_pixel_ladder_is_derived_from_scale_px(self):
        self.assertEqual(CONFIG_FONT_SIZE, scale_px(17, min_abs=12))
        self.assertEqual(
            DROPDOWN_ITEM_FONT_SIZE,
            max(scale_px(8, min_abs=8), CONFIG_FONT_SIZE - scale_px(2, min_abs=1)),
        )
        self.assertEqual(COMBO_DROP_WIDTH, scale_px(32, min_abs=28))

    def test_module_is_backend_neutral(self):
        module_path = Path(__file__).resolve().parents[1] / "lib" / "core" / "render" / "visuals" / "ai_settings_panel_visuals.py"
        names = []
        for node in ast.walk(ast.parse(module_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        for forbidden in ("PyQt5", "lib.script", "config.config_ui"):
            self.assertFalse(
                [name for name in names if name == forbidden or name.startswith(forbidden + ".")],
                forbidden,
            )


if __name__ == "__main__":
    unittest.main()
