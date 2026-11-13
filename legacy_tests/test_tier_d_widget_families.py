"""两个自包含 Qt 控件族下沉档位 D（第 73 轮收敛）。

`_GameCardWidget`（游戏卡片）与 `_BugTrackerWatermarkOverlay`（故障跟踪水印覆盖层）原本各自
住在 `lib/script/ui/game_manager_widgets.py` / `bug_tracker_widgets.py`。两者都是纯 Qt 控件：
它们自己就是 `QFrame` / `QWidget` 子类，做的是控件工具包事实（铺控件树、按 `QPainter` 自绘），
所以按《render 层边界契约》第 3 节的档位 D 判定下沉到 `backends/qt/widgets/`。

本模块钉住三件事：

- 产品侧只剩再导出/装配垫片，且不含 `PyQt5`；
- 宿主不 import `lib.script`（render 层规则），因此记录类型必须是鸭子类型而不是 `InstalledGame`；
- 字体取用入口由产品面注入，且注入的就是 `render_bridge` 的那一个（与 `forum_board` 同源）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCT = _REPO_ROOT / "lib" / "script" / "ui"
_HOST = _REPO_ROOT / "lib" / "core" / "render" / "backends" / "qt" / "widgets"


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class _StubManifest:
    def __init__(self, **overrides) -> None:
        self.game_id = "lahai_tetris"
        self.name = "拉海洛方块"
        self.version = "1.0.1"
        self.summary = "官方示例包"
        self.official = True
        self.particle_extensions = (object(),)
        self.effect_extensions = ()
        self.__dict__.update(overrides)


class _StubRecord:
    """鸭子类型记录：宿主不得依赖 `InstalledGame` 这个产品类。"""

    def __init__(self, **overrides) -> None:
        self.manifest = _StubManifest(**overrides)


class HostedWidgetFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_hosts_never_import_product_modules(self):
        for name in ("game_manager_widgets.py", "bug_tracker_widgets.py"):
            with self.subTest(module=name):
                names = _imported_names(_HOST / name)
                self.assertEqual(
                    [item for item in names if item == "lib.script" or item.startswith("lib.script.")],
                    [],
                )

    def test_product_shims_are_not_qt_implementations(self):
        for name in ("game_manager_widgets.py", "bug_tracker_widgets.py"):
            with self.subTest(module=name):
                path = _PRODUCT / name
                names = _imported_names(path)
                self.assertEqual(
                    [item for item in names if item == "PyQt5" or item.startswith("PyQt5.")],
                    [],
                )
                self.assertNotIn("QtWidgets", path.read_text(encoding="utf-8"))

    def test_product_names_resolve_to_the_host_classes(self):
        from lib.core.render.backends.qt.widgets.bug_tracker_widgets import (
            _BugTrackerWatermarkOverlay as HostOverlay,
        )
        from lib.core.render.backends.qt.widgets.game_manager_widgets import (
            _GameCardWidget as HostCard,
        )
        from lib.script.ui import bug_tracker_widgets as product_overlay
        from lib.script.ui import game_manager_widgets as product_card

        self.assertIs(product_overlay._BugTrackerWatermarkOverlay, HostOverlay)
        card = product_card._GameCardWidget(_StubRecord())
        self.assertIsInstance(card, HostCard)

    def test_font_seam_is_the_same_provider_the_bridge_exposes(self):
        from lib.core.render.backends.qt.widgets import bug_tracker_widgets as overlay_host
        from lib.core.render.backends.qt.widgets import game_manager_widgets as card_host
        from lib.script.ui import bug_tracker_widgets  # noqa: F401 - 导入即完成注入
        from lib.script.ui import game_manager_widgets  # noqa: F401 - 导入即完成注入
        from lib.script.ui import render_bridge

        # 产品垫片在导入时就完成注入，注入的是 bridge 的字体入口本身。
        self.assertIs(overlay_host._font_factory, render_bridge.digit_font)
        self.assertIs(card_host._font_factory, render_bridge.ui_font)

        ui_font = render_bridge.ui_font(size=13)
        digit_font = render_bridge.digit_font(size=13)
        self.assertEqual(
            (ui_font.family(), ui_font.pixelSize()),
            (card_host._ui_font(13).family(), card_host._ui_font(13).pixelSize()),
        )
        self.assertEqual(
            (digit_font.family(), digit_font.pixelSize()),
            (overlay_host._digit_font(13).family(), overlay_host._digit_font(13).pixelSize()),
        )

    def test_card_accepts_any_record_and_keeps_its_object_names(self):
        from lib.script.ui.game_manager_widgets import _GameCardWidget

        card = _GameCardWidget(_StubRecord())
        labels = {
            child.objectName(): child
            for child in card.findChildren(type(card.children()[0])) if child.objectName()
        }
        self.assertEqual(card.objectName(), "GameCard")
        self.assertTrue(card.sizeHint().isValid())
        self.assertIn("GameCardTitle", labels)
        self.assertIn("GameCardBadge", labels)

        card.set_selected(True)
        self.assertTrue(card.property("selected"))
        card.set_selected(False)
        self.assertFalse(card.property("selected"))

    def test_overlay_paints_once_the_host_carries_watermark_text(self):
        from PyQt5.QtWidgets import QWidget

        from lib.script.ui.bug_tracker_widgets import _BugTrackerWatermarkOverlay

        host = QWidget()
        self.addCleanup(host.deleteLater)
        host._watermark_title_text = "BUG\nTRACKER"
        host._watermark_hardware_text = "UnKnow GPU"
        host._watermark_meta_text = "CPU 0C"
        host._watermark_corner_text = "unknown"

        overlay = _BugTrackerWatermarkOverlay(host)
        self.addCleanup(overlay.deleteLater)
        overlay.resize(640, 420)

        pixmap = overlay.grab()
        self.assertFalse(pixmap.isNull())
        self.assertEqual((pixmap.width(), pixmap.height()), (640, 420))


if __name__ == "__main__":
    unittest.main()
