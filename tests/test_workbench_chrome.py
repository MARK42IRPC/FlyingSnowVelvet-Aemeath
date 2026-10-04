"""浮窗外壳收敛：后端中立数据与 Qt 宿主的分层与一致性。

外壳的"样式/主题判定"应当能在无 PyQt 的进程里计算（后端中立），而真实窗口
行为必须有唯一 Qt 落点。本模块把这个契约钉死：中立数据不得反向依赖产品模块，
抽出来的 QSS 必须与工作台实时 token 逐字符一致，旧导入名必须仍指向同一个宿主。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from lib.core.event.center import Event, EventType
from lib.core.render.visuals import workbench_chrome
from lib.core.render.visuals.workbench_chrome import (
    FLOATING_WINDOW_OBJECT_NAME,
    floating_window_stylesheet,
    is_workbench_theme_change,
)
from lib.script.workbench.theme import (
    get_workbench_colors,
    window_button_stylesheet,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NEUTRAL = _REPO_ROOT / "lib" / "core" / "render" / "visuals" / "workbench_chrome.py"


class WorkbenchChromeIsBackendNeutralTests(unittest.TestCase):
    def test_neutral_module_imports_neither_qt_nor_product_modules(self):
        tree = ast.parse(_NEUTRAL.read_text(encoding="utf-8-sig"), filename=str(_NEUTRAL))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        offenders = [
            name
            for name in names
            if name == "PyQt5"
            or name.startswith("PyQt5.")
            or name.startswith("lib.core.render.backends")
            or name.startswith("lib.script")
        ]
        self.assertEqual(offenders, [], "中立数据层不得依赖 Qt 或产品模块")

    def test_stylesheet_is_a_pure_function_of_the_resolved_mode(self):
        # 同一模式两次调用结果一致，且深浅两色不同——它是 token 的纯函数。
        self.assertEqual(
            floating_window_stylesheet("dark"), floating_window_stylesheet("dark")
        )
        self.assertNotEqual(
            floating_window_stylesheet("dark"), floating_window_stylesheet("light")
        )


class WorkbenchChromeMatchesTheLiveThemeTests(unittest.TestCase):
    def test_floating_qss_carries_the_window_button_block(self):
        # 浮窗 QSS 里嵌的窗口按钮样式必须与工作台实时 token 生成的一致。
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                self.assertIn(window_button_stylesheet(mode), floating_window_stylesheet(mode))

    def test_inlined_button_helper_matches_the_shared_helper(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    workbench_chrome._window_button_stylesheet(mode),
                    window_button_stylesheet(mode),
                )

    def test_floating_qss_paints_the_workbench_surface(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                colors = get_workbench_colors(mode)
                qss = floating_window_stylesheet(mode)
                self.assertIn(colors.surface_raised, qss)
                self.assertIn(colors.text, qss)
                self.assertIn(FLOATING_WINDOW_OBJECT_NAME, qss)


class WorkbenchChromeSeamTests(unittest.TestCase):
    def test_qt_host_and_factory_are_single_shared_implementations(self):
        from lib.core.render.backends.qt.widgets import floating_window, window_buttons
        from lib.script.ui import workbench_components, workbench_floating

        self.assertIs(
            workbench_floating.WorkbenchFloatingWindow,
            floating_window.QtWorkbenchFloatingWindow,
        )
        self.assertIs(
            workbench_floating.create_window_button, window_buttons.create_window_button
        )
        self.assertIs(
            workbench_components.create_window_button, window_buttons.create_window_button
        )
        # 旧路径仍然导出历史名，避免既有导入方静默失效。
        self.assertIs(
            workbench_floating.floating_window_stylesheet,
            workbench_chrome.floating_window_stylesheet,
        )

    def test_theme_change_detection(self):
        self.assertTrue(
            is_workbench_theme_change(
                Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"workbench_light_theme": True}}})
            )
        )
        self.assertFalse(
            is_workbench_theme_change(
                Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"other": 1}}})
            )
        )
        self.assertFalse(is_workbench_theme_change(Event(EventType.CONFIG_UPDATED, {})))
        self.assertFalse(is_workbench_theme_change(Event(EventType.TICK, None)))


if __name__ == "__main__":
    unittest.main()
