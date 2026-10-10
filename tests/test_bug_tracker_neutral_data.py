"""故障跟踪数据层与游戏运行时控制器已去 Qt（第 71 轮收敛）。

两处「数据 vs 工具包事实」的切分：

- `BugTrackerDataMixin` 只回答「这条记录属于哪个语义等级色名」，角色键与 `QColor`
  翻译留给 `BugTrackerWindow`（`_record_row_role` / `_color_for_level`）。
- `GameRuntime._on_clickthrough_toggle` 不再写 `Qt.WA_TransparentForMouseEvents`，
  改走 `render_bridge.set_widget_clickthrough`（与 `pointer_button_name` 同级的档位 A 落点）。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.script.bug_tracker.storage import BugRecord
from lib.script.ui.bug_tracker_data import BugTrackerDataMixin

REPO_ROOT = Path(__file__).resolve().parents[1]


def _record(**overrides) -> BugRecord:
    data = dict(
        timestamp="2026-10-10 12:00:00,000",
        logger="lib.example",
        level="ERROR",
        levelno=45,
        message="boom",
        pathname="C:/repo/lib/example.py",
        lineno=12,
        func_name="run",
        module="lib.example",
        process=1,
        thread_name="MainThread",
        exception="",
        stack_info="",
        instance_id="inst-1",
        instance_label="default",
        log_path="",
        raw={},
    )
    data.update(overrides)
    return BugRecord(**data)


class _StubItem:
    def __init__(self, role_value) -> None:
        self._role_value = role_value
        self.role_read = None

    def data(self, role):
        self.role_read = role
        return self._role_value


class _StubList:
    """最小 `QListWidget` 替身：只提供数据层用到的 `currentItem()`。"""

    def __init__(self, item) -> None:
        self._item = item

    def currentItem(self):  # noqa: N802 - 对齐 Qt 命名
        return self._item


class BugTrackerNeutralDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_level_colour_name_is_a_semantic_value_not_a_toolkit_colour(self):
        mixin = BugTrackerDataMixin()
        self.assertEqual(mixin._level_color_name(50), "danger")
        self.assertEqual(mixin._level_color_name(40), "danger")
        self.assertEqual(mixin._level_color_name(39), "warning")
        self.assertEqual(mixin._level_color_name(30), "warning")
        self.assertEqual(mixin._level_color_name(29), "cyan")
        self.assertEqual(mixin._level_color_name(0), "cyan")

    def test_selected_record_reads_the_host_injected_role(self):
        mixin = BugTrackerDataMixin()
        mixin._record_row_role = 4321
        mixin._records = [_record(message="first"), _record(message="second")]
        mixin._instance_filter = ""
        mixin._level_filters = {"info": True, "warn": True, "error": True}
        item = _StubItem(1)
        mixin._error_list = _StubList(item)

        self.assertEqual(mixin._selected_record().message, "second")
        self.assertEqual(item.role_read, 4321)

    def test_data_mixin_imports_without_pyqt(self):
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.script.ui.bug_tracker_data import BugTrackerDataMixin

            assert BugTrackerDataMixin()._level_color_name(45) == "danger"
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_window_injects_role_and_translates_the_semantic_colour(self):
        from PyQt5.QtCore import Qt

        import lib.script.ui.bug_tracker_window as window_module

        window = window_module.BugTrackerWindow.__new__(window_module.BugTrackerWindow)
        window._record_row_role = int(Qt.UserRole)
        window._level_color_name = lambda levelno: (
            "danger" if levelno >= 40 else "warning" if levelno >= 30 else "cyan"
        )

        danger = window._color_for_level(45)
        warning = window._color_for_level(35)
        cyan = window._color_for_level(10)

        self.assertEqual(danger.getRgb(), window_module._DANGER.getRgb())
        self.assertEqual(warning.getRgb(), window_module._WARNING.getRgb())
        self.assertEqual(cyan.getRgb(), window_module._CYAN.getRgb())
        self.assertNotEqual(danger.getRgb(), cyan.getRgb())

    def test_clickthrough_host_seam_sets_the_qt_attribute(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QWidget

        from lib.script.ui.render_bridge import set_widget_clickthrough

        widget = QWidget()
        self.addCleanup(widget.deleteLater)

        set_widget_clickthrough(widget, True)
        self.assertTrue(widget.testAttribute(Qt.WA_TransparentForMouseEvents))
        set_widget_clickthrough(widget, False)
        self.assertFalse(widget.testAttribute(Qt.WA_TransparentForMouseEvents))

    def test_game_runtime_controller_no_longer_imports_pyqt(self):
        source = (REPO_ROOT / "lib" / "script" / "ui" / "game_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("PyQt5", source)
        self.assertIn("set_widget_clickthrough", source)


if __name__ == "__main__":
    unittest.main()
