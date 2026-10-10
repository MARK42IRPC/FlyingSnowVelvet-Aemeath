"""工作台自绘件下沉到档位 D：宿主取色与产品侧 token 同源，产品只留再导出垫片。

`_WorkbenchFadeOverlay` / `_WorkbenchThemeToggle` 原在 `lib/script/ui/workbench_widgets.py`，
只依赖尺寸助手与工作台主题色，现在住进 `lib/core/render/backends/qt/widgets/workbench_widgets.py`。

关键约束：render 层不得 import `lib.script`，因此宿主不能继续调
`lib.script.workbench.theme.get_workbench_colors()`，改读
`lib.core.render.visuals.workbench_tokens` 的 token。本模块钉住两条等价：

- 宿主调色板与产品侧 `WorkbenchColors` 逐值相同（深浅两模式）；
- 产品侧模块不再是 Qt 实现，只把名字转发给宿主，且不再 import `PyQt5`。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from lib.core.render.backends.qt.widgets import workbench_widgets as host
from lib.core.render.visuals.workbench_tokens import WORKBENCH_TOKEN_NAMES
from lib.script.workbench.theme import DARK_COLORS, LIGHT_COLORS
from lib.script.ui import workbench_widgets as product

_REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkbenchOverlayHostTests(unittest.TestCase):
    def test_host_palette_matches_the_product_theme_value_for_value(self):
        for mode, colors in (("dark", DARK_COLORS), ("light", LIGHT_COLORS)):
            with self.subTest(mode=mode):
                palette = host._workbench_palette(mode)
                self.assertEqual(
                    {name: palette[name] for name in WORKBENCH_TOKEN_NAMES},
                    {name: getattr(colors, name) for name in WORKBENCH_TOKEN_NAMES},
                )

    def test_host_palette_tracks_the_live_mode_when_none_is_given(self):
        from lib.core.render.visuals.workbench_tokens import resolve_workbench_mode

        self.assertEqual(
            host._workbench_palette(),
            host._workbench_palette(resolve_workbench_mode()),
        )

    def test_product_module_is_only_a_reexport_shim_without_qt(self):
        path = _REPO_ROOT / "lib" / "script" / "ui" / "workbench_widgets.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertEqual(
            [name for name in imported if name == "PyQt5" or name.startswith("PyQt5.")],
            [],
        )
        self.assertNotIn("QtWidgets", path.read_text(encoding="utf-8"))

    def test_product_names_are_the_host_classes(self):
        self.assertIs(product._WorkbenchFadeOverlay, host._WorkbenchFadeOverlay)
        self.assertIs(product._WorkbenchThemeToggle, host._WorkbenchThemeToggle)


if __name__ == "__main__":
    unittest.main()
