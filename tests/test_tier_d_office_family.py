"""办公族与世界对象整族出列（第 78 轮，批次 3）。

`lib/script/ui` 的办公族与七个世界对象按《render 层边界契约》第 3 节档位归类后一次性出列：

- 办公 Qt 件：`office_approval_controller` / `office_chat_view` / `office_effort_slider` /
  `office_manager_card` / `office_mode_page` / `office_page` → `backends/qt/widgets/`；
- 七个世界对象：`world_objects/{clock,motor,snow_leopard,snow_pile,snowball,sofa,speaker}.py`
  → `backends/qt/widgets/`；
- 产品侧一律留再导出垫片；`office_style.py` 本就零 Qt，只是从冻结清单出列。

本模块钉住族级不变量：

- 产品垫片都不含 `PyQt5`，也不静态 import 档位 D（名字与 seam 注入都经 `render_bridge`）；
- 档位 D 宿主不 import `lib.script`；
- 世界对象的声音落在产品侧唯一入口 `world_objects/_sounds.py`，且它不含 `PyQt5`；
- 关键公开符号解析到宿主，`:class:OfficeWorkbenchPage` 与 `:class:OfficeModePage` 仍是
  各自 `backends/qt/widgets/` 宿主实现。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT_UI = _REPO_ROOT / "lib" / "script" / "ui"
_PRODUCT_WORLD = _PRODUCT_UI / "world_objects"
_TIER_D = _REPO_ROOT / "lib" / "core" / "render" / "backends" / "qt" / "widgets"

#: 办公族产品垫片：都不得含 PyQt5、不得静态 import 档位 D。
_PRODUCT_OFFICE_SHIMS = (
    "office_approval_controller.py",
    "office_chat_view.py",
    "office_effort_slider.py",
    "office_manager_card.py",
    "office_mode_page.py",
    "office_page.py",
)
#: 七个世界对象产品垫片。
_PRODUCT_WORLD_SHIMS = tuple(
    f"{name}.py"
    for name in (
        "clock",
        "motor",
        "snow_leopard",
        "snow_pile",
        "snowball",
        "sofa",
        "speaker",
    )
)
#: 对应档位 D 宿主。
_TIER_D_FILES = _PRODUCT_OFFICE_SHIMS + _PRODUCT_WORLD_SHIMS


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class OfficeFamilyTests(unittest.TestCase):
    def test_product_shims_are_not_qt_or_tier_d_implementations(self):
        for name in _PRODUCT_OFFICE_SHIMS:
            with self.subTest(module=name):
                names = _imported_names(_PRODUCT_UI / name)
                self.assertEqual(
                    [n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [], name
                )
                self.assertEqual(
                    [n for n in names if n.startswith("lib.core.render.backends.qt")],
                    [],
                    f"{name} 垫片不得静态 import 档位 D",
                )

    def test_world_object_shims_are_not_qt_or_tier_d_implementations(self):
        for name in _PRODUCT_WORLD_SHIMS:
            with self.subTest(module=name):
                names = _imported_names(_PRODUCT_WORLD / name)
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

    def test_sound_factory_is_the_only_product_voice_entry(self):
        """世界对象的声音全经 `_sounds.py` 分派；它本身不得 import PyQt5。"""
        names = _imported_names(_PRODUCT_WORLD / "_sounds.py")
        self.assertEqual([n for n in names if n == "PyQt5" or n.startswith("PyQt5.")], [])
        self.assertEqual(
            [n for n in names if n.startswith("lib.core.render.backends.qt")], []
        )
        # 七个世界对象垫片都不直接 import 声音类。
        for name in _PRODUCT_WORLD_SHIMS:
            with self.subTest(module=name):
                shim_names = _imported_names(_PRODUCT_WORLD / name)
                self.assertEqual(
                    [n for n in shim_names if n.startswith("lib.script.voice")], [], name
                )

    def test_public_names_resolve_to_the_host(self):
        from lib.core.render.backends.qt.widgets import office_mode_page as host_mode
        from lib.core.render.backends.qt.widgets import office_page as host_page
        from lib.script.ui import office_mode_page as product_mode
        from lib.script.ui import office_page as product_page

        self.assertIs(product_page.OfficeWorkbenchPage, host_page.OfficeWorkbenchPage)
        self.assertIs(product_mode.OfficeModePage, host_mode.OfficeModePage)

    def test_world_object_classes_resolve_to_the_host(self):
        from lib.core.render.backends.qt.widgets import snow_pile as host_pile
        from lib.script.ui.world_objects import snow_pile as product_pile

        self.assertIs(product_pile.SnowPile, host_pile.SnowPile)

    def test_world_object_sound_seam_defaults_to_silence(self):
        """未注入声音工厂时，世界对象声音退化为静音对象而不是抛错。"""
        from lib.core.render.backends.qt.widgets import tier_d_seams

        sound = tier_d_seams.world_object_sound("definitely-not-a-sound")
        self.assertTrue(callable(sound.play))
        sound.play()  # 不得抛错


if __name__ == "__main__":
    unittest.main()
