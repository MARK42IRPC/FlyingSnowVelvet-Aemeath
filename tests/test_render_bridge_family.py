"""批次 4 收尾守卫：`lib/script/ui` 零 Qt、转发面收进 render 层（第 79 节）。

`render_bridge` 的下沉是本轮唯一"非纯移动"：实现搬进 `lib/core/render/qt_bridge.py`，
产品侧只留零 Qt 薄垫片。本模块把该轮的不变量钉成可执行断言：

- `frozen_ui_qt_importers` 归零，且全仓 `import PyQt5` 只剩 `lib/core/render/` 内
  加 5 个文档化的入口 / 构建例外；
- 产品垫片与转发器零 `PyQt5`、零档位 A/B/D 路径字面量；
- 核心转发层不反向 import 产品包；
- 转发面的具名导入与模块属性访问两条路径都解析到核心实现（含补丁友好性）；
- 两个手工成员（`apply_settings_page_fonts` / `configure_forum_window_violation_sound`）
  留在产品侧并照常可用。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_UI = _REPO_ROOT / "lib" / "script" / "ui"
_CORE_BRIDGE = _REPO_ROOT / "lib" / "core" / "render" / "qt_bridge.py"

#: 与 `test_qt_dependency_boundaries` 的 `allowed_files` 同源：进 payload 之外的入口 /
#: 构建工具，以及官方游戏包 v1 的既有例外。
_DOCUMENTED_QT_FILES = frozenset(
    {
        "lib/script/app/qt_backend_bootstrap.py",
        "lib/script/app/qt_application_ui.py",
        "lib/script/app/workbench_helper_entry.py",
        "lib/script/bug_tracker/__main__.py",
        "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/render.py",
        "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/widget.py",
        "scripts/payload_selftest.py",
    }
)

#: 档位 A / B / D 的路径前缀；产品侧不允许出现任何一个。
_TIER_PREFIXES = (
    "lib.core.render.backends.qt.drawing",
    "lib.core.render.backends.qt.runtime",
    "lib.core.render.backends.qt.widgets",
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


def _qt_importers(paths) -> set[str]:
    found = set()
    for path in paths:
        names = _imported_names(path)
        if any(name == "PyQt5" or name.startswith("PyQt5.") for name in names):
            found.add(path.relative_to(_REPO_ROOT).as_posix())
    return found


class RenderBridgeFamilyTests(unittest.TestCase):
    def test_ui_package_is_entirely_free_of_qt_imports(self):
        """`lib/script/ui` 下任何文件（含转发器）都不得 `import PyQt5`。"""
        self.assertEqual(_qt_importers(sorted(_UI.rglob("*.py"))), set())

    def test_repository_qt_imports_only_live_inside_the_render_layer(self):
        """终局核对：Qt 导入只剩 `lib/core/render/` 内 + 文档化例外。"""
        offenders = set()
        for base in ("config", "lib", "scripts"):
            offenders |= _qt_importers(sorted((_REPO_ROOT / base).rglob("*.py")))
        offenders = {
            path
            for path in offenders
            if not path.startswith("lib/core/render/")
            and path not in _DOCUMENTED_QT_FILES
        }
        self.assertEqual(offenders, set())

    def test_product_shims_are_neither_qt_nor_tier_paths(self):
        for name in ("render_bridge.py",):
            with self.subTest(module=name):
                names = _imported_names(_UI / name)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [], name
                )
                self.assertEqual(
                    [n for n in names if any(n.startswith(p) for p in _TIER_PREFIXES)],
                    [],
                    f"{name} 不得出现档位 A/B/D 路径",
                )

    def test_core_bridge_never_imports_product_modules(self):
        """核心转发层在 render 层内，因此不得反向 import `lib.script`。"""
        names = _imported_names(_CORE_BRIDGE)
        self.assertEqual(
            [n for n in names if n == "lib.script" or n.startswith("lib.script.")], []
        )

    def test_forwarded_names_are_the_core_objects(self):
        """转发是"同一函数对象"的别名，不是包装器：签名与来源必须与核心一致。"""
        import inspect

        from lib.core.render import qt_bridge
        from lib.script.ui import render_bridge

        for name in ("ui_font", "text_metrics", "create_control_host", "resolve_placement",
                     "tier_d_reexports", "qt_color"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(render_bridge, name), name)
                # 别名而非包装：对象身份相同，签名 / 文档 / 模块也随之相同。
                self.assertIs(getattr(render_bridge, name), getattr(qt_bridge, name), name)
        # 具名转发只可能是包装器才会吞掉的签名，这里必须与核心逐字一致。
        self.assertEqual(
            str(inspect.signature(render_bridge.create_spec_window)),
            str(inspect.signature(qt_bridge.create_spec_window)),
        )
        self.assertEqual(len(render_bridge.__all__), len(qt_bridge.__all__) + 2)

    def test_getattr_fallback_does_not_shadow_patched_attributes(self):
        """`__all__` 内的名字在模块命名空间里，补丁只改垫片、不泄漏到核心层。"""
        from unittest import mock

        from lib.core.render import qt_bridge
        from lib.script.ui import render_bridge

        sentinel = object()
        with mock.patch.object(render_bridge, "ui_font", sentinel):
            self.assertIs(render_bridge.ui_font, sentinel)
            self.assertIsNot(qt_bridge.ui_font, sentinel)
        self.assertIs(render_bridge.ui_font, qt_bridge.ui_font)

    def test_manual_members_stay_on_the_product_side(self):
        """两个要 import 产品包的成员留在垫片，而不是核心层。"""
        tree = ast.parse(_CORE_BRIDGE.read_text(encoding="utf-8"))
        core_defs = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        for name in ("apply_settings_page_fonts", "configure_forum_window_violation_sound"):
            self.assertNotIn(name, core_defs, f"{name} 不应出现在核心层")

        shim_tree = ast.parse((_UI / "render_bridge.py").read_text(encoding="utf-8"))
        shim_defs = {
            node.name for node in shim_tree.body if isinstance(node, ast.FunctionDef)
        }
        for name in ("apply_settings_page_fonts", "configure_forum_window_violation_sound"):
            self.assertIn(name, shim_defs, f"{name} 必须留在产品垫片")

    def test_forum_violation_sound_seam_reaches_the_host(self):
        from lib.core.render.backends.qt.widgets import forum_window
        from lib.script.ui import render_bridge

        original = forum_window._violation_sound_factory
        try:
            render_bridge.configure_forum_window_violation_sound()
            self.assertIsNotNone(forum_window._violation_sound_factory)
            sound = forum_window._violation_sound_factory()
            self.assertTrue(hasattr(sound, "play"))
        finally:
            forum_window.configure_violation_sound_factory(original)


if __name__ == "__main__":
    unittest.main()
