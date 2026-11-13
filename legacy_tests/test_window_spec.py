"""窗口级描述的收敛：描述层不碰 Qt，产品窗口只交描述，Qt 宿主按描述装配。

本轮补上「单个叶控件描述」（``visuals/controls.py``）之上的一级缺口：**一个窗口长什么样**。
这里钉住四件事：

- 描述层（`window_spec.py` / `window_specs.py`）能在 PyQt5 被屏蔽的进程里独立工作；
- 中立模块不 import `lib.script`、也不直接引用任一后端；
- 审批弹窗的控件树、对象名、图标与语义回调确实由描述产出，而不是产品模块自己搭；
- 生产 ``frozen_ui_qt_importers`` / 落位清单的收缩与迁移是同一件事。

**注意**：产品面模块一律在测试方法内 import（``office_style`` 会经 ``config`` 固化主题）。
"""
from __future__ import annotations

import ast
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="window-spec-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))


_DESCRIPTION_SCRIPT = textwrap.dedent(
    """
    import builtins
    import sys

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "PyQt5" or name.startswith("PyQt5."):
            raise AssertionError(f"window description imported Qt: {name}")
        return original_import(name, *args, **kwargs)

    builtins.__import__ = blocked_import

    from lib.core.render.visuals.window_spec import (
        ButtonSpec,
        LabelSpec,
        LayoutSpec,
        WindowSpec,
        button_specs,
        find_spec,
        iter_specs,
        retarget_text,
    )
    from lib.core.render.visuals.window_specs import (
        help_window_spec,
        office_approval_window_spec,
    )

    spec = office_approval_window_spec(
        title="shell",
        reason="需要执行命令",
        command_text='{"command": "npm test"}',
        min_width=500,
        max_width=680,
    )
    assert isinstance(spec, WindowSpec)
    assert spec.object_name == "OfficeApprovalDialog"
    assert spec.drag_handle_ids == ("header",)
    assert spec.close_semantic == "reject"

    # 条件子控件是描述层的一等公民：有命令才有预览区。
    assert find_spec(spec, "command") is not None
    without_command = office_approval_window_spec(title="t", reason="r")
    assert find_spec(without_command, "command") is None
    assert find_spec(without_command, "command_label") is None

    # 遍历与查询在后端中立层完成。
    ids = [node.id for node in iter_specs(spec) if getattr(node, "id", "")]
    assert ids[0] == "root" and "accent_bar" in ids and "actions" in ids
    assert [button.semantic for button in button_specs(spec.content)] == [
        "reject", "reject", "allow", "allow_task"
    ]

    # 换内容只重建一条路径，其余节点共享。
    changed = retarget_text(spec, "reason", "换过了")
    assert find_spec(changed, "reason").text == "换过了"
    assert find_spec(spec, "reason").text == "需要执行命令"
    assert changed.content.children[0] is spec.content.children[0]

    help_spec = help_window_spec(
        title="帮助",
        text="正文",
        width=440,
        height=380,
        empty_text="兜底",
        stylesheet="QWidget#DesktopPetHelpDialog { color: {color:text}; }",
    )
    assert help_spec.object_name == "DesktopPetHelpDialog"
    assert help_spec.kind == "tool"
    assert help_spec.fixed_size == (440, 380)
    assert help_spec.layer == "dialog" and help_spec.fade is True
    assert help_spec.hide_semantics == ("close",)
    assert help_spec.border_frame is True
    assert find_spec(help_spec, "body").text == "正文"
    assert find_spec(help_spec, "body").plain_text is True
    assert find_spec(help_spec, "body_area").scroll is True
    assert find_spec(help_spec, "header").text == "帮助"
    assert find_spec(help_spec, "close").tool_button is True
    # 空正文走兜底文案，不会开出一片空白。
    assert find_spec(help_window_spec(title="t", text="  ", empty_text="兜底"), "body").text == "兜底"

    assert [name for name in sys.modules if name.startswith("PyQt5")] == []
    """
)


def _imports_of(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class WindowSpecDescriptionTests(unittest.TestCase):
    def test_description_layer_works_without_pyqt(self):
        result = subprocess.run(
            [sys.executable, "-c", _DESCRIPTION_SCRIPT],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_neutral_description_modules_stay_out_of_product_and_toolkit(self):
        """描述层不得 import `lib.script`，也不得直接引用任一后端。"""

        banned_prefixes = (
            "PyQt5",
            "lib.script",
            "lib.core.render.backends.qt",
            "lib.core.render.backends.dx",
            "config.",
        )
        for name in ("window_spec.py", "window_specs.py"):
            with self.subTest(module=name):
                for imported in _imports_of(
                    _REPO_ROOT / "lib" / "core" / "render" / "visuals" / name
                ):
                    self.assertFalse(
                        any(
                            imported == prefix or imported.startswith(prefix + ".")
                            for prefix in banned_prefixes
                        ),
                        f"{name} imports {imported}",
                    )

    def test_qt_host_does_not_reach_into_the_drawing_tier(self):
        """窗口宿主是档位 D：绘制档由 `render_bridge` 注入，不得被它静态引用。"""

        imported = _imports_of(
            _REPO_ROOT
            / "lib"
            / "core"
            / "render"
            / "backends"
            / "qt"
            / "widgets"
            / "spec_host.py"
        )
        self.assertFalse(
            [
                name
                for name in imported
                if name == "lib.core.render.backends.qt.drawing"
                or name.startswith("lib.core.render.backends.qt.drawing.")
            ],
            "spec_host 静态引用了档位 A",
        )


class OfficeApprovalSpecTests(unittest.TestCase):
    """审批弹窗已迁到「描述 + 后端渲染」：控件树与语义都由描述产出。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _approval() -> dict:
        return {
            "task_id": "task-1",
            "approval_id": "approval-1",
            "tool_name": "shell",
            "reason": "需要执行命令",
            "command": {"command": "npm test"},
        }

    def _make(self, approval: dict | None = None):
        from lib.script.ui.office_approval_dialog import OfficeApprovalDialog

        dialog = OfficeApprovalDialog(approval if approval is not None else self._approval())
        self.addCleanup(dialog.close)
        return dialog

    def test_dialog_tree_comes_from_the_window_description(self):
        from PyQt5.QtWidgets import QLabel, QPlainTextEdit, QToolButton, QWidget

        from lib.core.render.visuals.window_spec import find_spec
        from lib.script.ui.office_style import office_stylesheet

        dialog = self._make()
        widget = dialog.widget()
        self.assertEqual(widget.objectName(), "OfficeApprovalDialog")
        self.assertIsNotNone(widget.findChild(QWidget, "OfficeAccentBar"))
        self.assertIsNotNone(widget.findChild(QToolButton, "OfficeApprovalClose"))
        self.assertIsNotNone(widget.findChild(QLabel, "OfficeApprovalTitle"))
        self.assertEqual(
            widget.findChild(QLabel, "OfficeApprovalTitle").text(), "shell"
        )
        command = widget.findChild(QPlainTextEdit, "OfficeApprovalCommand")
        self.assertIsNotNone(command)
        self.assertEqual(command.toPlainText(), '{\n  "command": "npm test"\n}')
        self.assertEqual(widget.styleSheet(), office_stylesheet())
        # 命令预览是描述里的可选节点，去掉载荷就不该出现。
        self.assertIsNotNone(find_spec(dialog._spec, "command"))

    def test_dialog_omits_the_command_preview_without_a_command(self):
        from PyQt5.QtWidgets import QPlainTextEdit

        approval = self._approval()
        approval.pop("command")
        dialog = self._make(approval)
        self.assertIsNone(
            dialog.widget().findChild(QPlainTextEdit, "OfficeApprovalCommand")
        )

    def test_buttons_emit_the_three_decisions_through_the_description(self):
        from PyQt5.QtWidgets import QPushButton

        for object_name, expected in (
            ("OfficeApprovalReject", "reject"),
            ("OfficeApprovalAllow", "allow"),
            ("OfficeApprovalAllowTask", "allow_task"),
        ):
            with self.subTest(button=object_name):
                dialog = self._make()
                seen: list[tuple[str, str]] = []
                dialog.decision_made.connect(lambda a, d: seen.append((a, d)))
                button = dialog.findChild(QPushButton, object_name)
                self.assertIsNotNone(button, object_name)
                button.click()
                self.app.processEvents()
                self.assertEqual(seen, [("approval-1", expected)])

    def test_frameless_dialog_keeps_no_window_caption(self):
        from PyQt5.QtCore import Qt

        dialog = self._make()
        self.assertTrue(dialog.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(dialog.testAttribute(Qt.WA_StyledBackground))
        self.assertTrue(dialog.testAttribute(Qt.WA_DeleteOnClose))

    def test_dismiss_without_decision_does_not_emit(self):
        dialog = self._make()
        seen: list[tuple[str, str]] = []
        dialog.decision_made.connect(lambda a, d: seen.append((a, d)))
        dialog.dismiss_without_decision()
        self.app.processEvents()
        self.assertEqual(seen, [])

    def test_built_dialog_is_centered_on_its_reference(self):
        """居中落位复用 `visuals/layout.py` 的中心对中心：给定屏幕即可逐值核对。"""

        from lib.core.render.visuals.types import Rect

        class _FixedScreen:
            def screen_rect_for_widget(self, widget=None, point=None):
                return Rect(0, 0, 1920, 1080)

            def widget_global_rect(self, widget):
                return Rect(0, 0, 800, 600)

        dialog = self._make()
        window = dialog._window
        window._presentation = _FixedScreen()
        reference = Rect(1000, 400, 800, 600)
        window.center_on(reference)
        widget = window.widget
        self.assertEqual(widget.x(), 1400 - widget.width() // 2)
        self.assertEqual(widget.y(), 700 - widget.height() // 2)


class WindowSpecMigrationGuardTests(unittest.TestCase):
    def test_approval_dialog_left_the_frozen_qt_importers(self):
        source = (
            _REPO_ROOT / "tests" / "test_qt_dependency_boundaries.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"lib/script/ui/office_approval_dialog.py"', source)
        entry = '("lib/script/ui/office_approval_dialog.py", "OfficeApprovalDialog")'
        migrated = (
            _REPO_ROOT / "tests" / "test_control_layer_descriptions.py"
        ).read_text(encoding="utf-8")
        self.assertIn(entry, migrated)


class HelpWindowSpecTests(unittest.TestCase):
    """帮助浮窗已迁到「描述 + 后端渲染」：工具窗标志、滚动视口、淡入淡出与关闭语义
    都由窗口描述产出，产品模块只收集「标题 + 正文」。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _make(self):
        from lib.script.ui.help_window import DesktopPetHelpDialog

        dialog = DesktopPetHelpDialog()
        self.addCleanup(dialog.cleanup)
        return dialog

    def test_tool_window_flags_and_fixed_size_come_from_the_description(self):
        from PyQt5.QtCore import Qt

        dialog = self._make()
        dialog.show_help("\u6807\u9898", "\u6b63\u6587")
        flags = dialog.widget().windowFlags()
        self.assertTrue(flags & Qt.Tool)
        self.assertTrue(flags & Qt.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowStaysOnTopHint)
        self.assertTrue(dialog.widget().testAttribute(Qt.WA_TranslucentBackground))
        self.assertEqual(dialog.widget().width(), dialog.widget().minimumWidth())
        self.assertEqual(
            (dialog.widget().width(), dialog.widget().height()),
            (dialog._spec.fixed_size[0], dialog._spec.fixed_size[1]),
        )

    def test_body_is_plain_text_and_the_tree_comes_from_the_description(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel, QScrollArea

        dialog = self._make()
        dialog.show_help("\u7b2c\u4e00\u8282", "<b>\u4e0d\u662f\u5bcc\u6587\u672c</b>")
        self.assertEqual(dialog._body.textFormat(), Qt.PlainText)
        self.assertEqual(dialog._body.text(), "<b>\u4e0d\u662f\u5bcc\u6587\u672c</b>")
        scroll = dialog.widget().findChild(QScrollArea, "HelpScroll")
        self.assertIsNotNone(scroll)
        self.assertIs(dialog._scroll, scroll)
        self.assertIsNotNone(dialog.widget().findChild(QLabel, "HelpBody"))
        self.assertIsNotNone(dialog.widget().findChild(QLabel, "HelpHeader"))

    def test_empty_text_falls_back_to_the_placeholder(self):
        from lib.script.ui.help_window import HELP_EMPTY_TEXT

        dialog = self._make()
        dialog.show_help("\u53ea\u6709\u6807\u9898", "   ")
        self.assertEqual(dialog._body.text(), HELP_EMPTY_TEXT)

    def test_close_button_hides_the_window_instead_of_ending_it(self):
        dialog = self._make()
        dialog.show_help("\u6807\u9898", "\u6b63\u6587")
        self.assertTrue(dialog.wants_visible())
        dialog._close_button.click()
        self.app.processEvents()
        self.assertFalse(dialog.wants_visible())
        # 窗口本体仍在（清理只由 cleanup() 负责），再次换内容会重新淡入。
        dialog.show_help("\u6807\u9898", "\u65b0\u6b63\u6587")
        self.assertTrue(dialog.wants_visible())
        self.assertEqual(dialog._body.text(), "\u65b0\u6b63\u6587")

    def test_shell_layout_matches_the_pre_migration_widget(self):
        """迁移 oracle：壳层几何逐项对齐收敛前的 `help_window.py`。

        这几条关系式取自收敛前 Qt 控件实测的几何（`HEAD` 版本），跑偏即失败：
        窗眉是固定高的整行、关闭按钮贴着窗眉顶、accent 竖条在窗眉里垂直居中、
        正文住在滚动视口里而不是直接挂在窗口上。
        """
        from PyQt5.QtWidgets import QFrame, QToolButton, QWidget

        dialog = self._make()
        dialog.show_help("\u6807\u9898", "\u6b63\u6587")
        widget = dialog.widget()

        header_row = widget.findChild(QWidget, "HelpHeaderRow")
        accent = widget.findChild(QFrame, "HelpHeaderAccent")
        close = widget.findChild(QToolButton, "HelpCloseButton")
        host = widget.findChild(QWidget, "HelpScrollHost")
        self.assertIsNotNone(header_row)
        self.assertIsNotNone(accent)
        self.assertIsNotNone(close)
        self.assertIsNotNone(host)

        # 窗眉：固定高度，关闭按钮贴顶，accent 竖条居中。
        self.assertEqual(header_row.minimumHeight(), header_row.maximumHeight())
        self.assertEqual(close.geometry().top(), 0)
        self.assertEqual(
            accent.geometry().top() + accent.geometry().height() // 2,
            header_row.height() // 2,
        )
        # accent 在关闭按钮左侧，标题列被推到 accent 右边。
        self.assertLess(accent.geometry().left(), close.geometry().left())
        # 正文住在滚动视口的内部承载控件里。
        self.assertIs(dialog._body.parent(), host)
        self.assertIs(dialog._scroll.widget(), host)

    def test_window_registers_into_the_dialog_layer(self):
        from lib.core.render.layers import get_layer_manager

        dialog = self._make()
        dialog.show_help("\u6807\u9898", "\u6b63\u6587")
        names = [entry[3] for entry in get_layer_manager().snapshot()]
        self.assertIn("DesktopPetHelpDialog", names)

    def test_cleanup_unregisters_the_layer_and_is_safe_to_repeat(self):
        from lib.core.render.layers import get_layer_manager

        dialog = self._make()
        dialog.show_help("\u6807\u9898", "\u6b63\u6587")
        dialog.cleanup()
        dialog.cleanup()
        self.app.processEvents()
        names = [entry[3] for entry in get_layer_manager().snapshot()]
        self.assertNotIn("DesktopPetHelpDialog", names)


class HelpWindowMigrationGuardTests(unittest.TestCase):
    def test_help_window_left_the_leaf_control_placement_list(self):
        source = (
            _REPO_ROOT / "tests" / "test_render_layout_algorithms.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"help_window.py",', source)


if __name__ == "__main__":
    unittest.main()
