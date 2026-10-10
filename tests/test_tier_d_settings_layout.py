"""工作台设置页的共享布局原语下沉档位 D（第 74 轮收敛）。

`lib/script/ui/workbench_settings_layout.py` 里住的五个 `QWidget` 骨架
（`SettingsFormLayout` / `SettingsPageHeader` / `SettingsSection` / `SettingsActionBar` /
`SettingsPageScaffold`）与 `apply_settings_page_fonts()` 是「产品页面要继承/调用的控件
工具包事实」，已下沉到 `lib/core/render/backends/qt/widgets/workbench_settings_layout.py`。

本模块钉住：

- 宿主不 import `lib.script`，产品垫片不含 `PyQt5`；
- 产品面的名字与三个字号档常量仍解析到宿主（调用方导入面不变）；
- 字体入口就是 `render_bridge.ui_font` 本身；
- 布局契约不变：`addRow("", widget)` 仍补出零高占位标签把标签列留住、帮助文案空时问号收起、
  状态文案出现时动作条才显形。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT = _REPO_ROOT / "lib" / "script" / "ui" / "workbench_settings_layout.py"
_HOST = (
    _REPO_ROOT
    / "lib"
    / "core"
    / "render"
    / "backends"
    / "qt"
    / "widgets"
    / "workbench_settings_layout.py"
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


class SettingsLayoutHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

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
        from lib.core.render.backends.qt.widgets import workbench_settings_layout as host
        from lib.script.ui import workbench_settings_layout as product

        for name in (
            "SettingsFormLayout",
            "SettingsPageHeader",
            "SettingsSection",
            "SettingsActionBar",
            "SettingsPageScaffold",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(product, name), getattr(host, name))
        self.assertIs(product.apply_settings_page_fonts, host.apply_settings_page_fonts)
        self.assertIs(product.create_settings_form, host.SettingsFormLayout)

    def test_font_sizes_are_the_same_values_and_the_font_seam_is_the_bridge_entry(self):
        from lib.core.render.backends.qt.widgets import workbench_settings_layout as host
        from lib.script.ui import render_bridge
        from lib.script.ui import workbench_settings_layout as product

        self.assertEqual(
            (product.SETTINGS_LABEL_WIDTH, product.SETTINGS_FONT_SIZE, product.SETTINGS_HINT_FONT_SIZE),
            (host.SETTINGS_LABEL_WIDTH, host.SETTINGS_FONT_SIZE, host.SETTINGS_HINT_FONT_SIZE),
        )
        self.assertIs(host._font_factory, render_bridge.ui_font)
        expected = render_bridge.ui_font(size=host.SETTINGS_FONT_SIZE)
        actual = host._ui_font(host.SETTINGS_FONT_SIZE)
        self.assertEqual(
            (actual.family(), actual.pixelSize()),
            (expected.family(), expected.pixelSize()),
        )

    def test_scroll_factory_reexport_is_the_tier_d_implementation(self):
        from lib.core.render.backends.qt.widgets.smooth_scroll import SmoothScrollArea as Host
        from lib.script.ui import workbench_settings_layout as product

        self.assertIs(product.SmoothScrollArea, Host)

    def test_label_less_row_still_reserves_the_label_column(self):
        from PyQt5.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

        from lib.script.ui.workbench_settings_layout import SettingsFormLayout

        page = QWidget()
        self.addCleanup(page.deleteLater)
        outer = QVBoxLayout(page)
        form = SettingsFormLayout()
        outer.addLayout(form)
        form.addRow("标签", QLabel("字段"))
        form.addRow("", QLabel("字段2"))
        page.resize(400, 200)
        page.show()
        self.app.processEvents()

        labels = [
            form.itemAt(row, QFormLayout.LabelRole).widget()
            for row in range(form.rowCount())
        ]
        # 无标签行由 `_normalize_row()` 补出零高占位标签：宽度与有标签行一致（都是标签列宽），
        # 高度为 0，且**没有隐藏**（隐藏的标签不参与标签列测量）。
        from lib.script.ui.workbench_settings_layout import SETTINGS_LABEL_WIDTH

        self.assertEqual({label.width() for label in labels}, {SETTINGS_LABEL_WIDTH})
        self.assertEqual({label.objectName() for label in labels}, {"ConfigFormLabel"})
        self.assertEqual(labels[1].height(), 0)
        self.assertTrue(labels[1].isVisibleTo(page))
        page.hide()

    def test_help_button_tracks_the_help_text_and_action_bar_shows_on_status(self):
        from PyQt5.QtWidgets import QWidget

        from lib.script.ui.workbench_settings_layout import (
            SettingsActionBar,
            SettingsSection,
        )

        host = QWidget()
        self.addCleanup(host.deleteLater)
        section = SettingsSection("分区", "说明", host)
        self.assertFalse(section.help_button.isVisible())
        section.set_help_text("帮助")
        self.assertTrue(section.help_button.isVisibleTo(section))
        self.assertEqual(section.help_text(), "帮助")

        bar = SettingsActionBar(host)
        self.assertFalse(bar.isVisible())
        bar.set_status("状态")
        self.assertTrue(bar.isVisibleTo(host))


if __name__ == "__main__":
    unittest.main()
