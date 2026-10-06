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
    from lib.core.render.visuals.window_specs import office_approval_window_spec

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


if __name__ == "__main__":
    unittest.main()
