from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="office-approval-controller-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QLabel, QPlainTextEdit, QWidget

from lib.script.office.ipc import OfficeFileIpc
from lib.script.ui.office_approval_controller import OfficeApprovalController
from lib.script.ui.office_style import office_stylesheet
from lib.script.workbench.theme import get_workbench_colors


class OfficeApprovalControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _approval(approval_id: str) -> dict:
        return {
            "task_id": "task-1",
            "approval_id": approval_id,
            "tool_name": "shell",
            "reason": "需要执行命令",
            "command": {"command": "npm test"},
        }

    def test_allow_task_is_submitted_once_for_same_pending_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir))
            approval = self._approval("approval-1")
            ipc.publish({"pending_approval": approval}, [])
            controller = OfficeApprovalController(ipc=ipc)
            self.addCleanup(controller.cleanup)

            controller.start()
            dialog = controller.active_dialog
            self.assertIsNotNone(dialog)
            dialog._resolve("allow_task")
            self.app.processEvents()

            commands = ipc.consume()
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0]["command"], "approval")
            self.assertEqual(commands[0]["data"]["approval_id"], "approval-1")
            self.assertEqual(commands[0]["data"]["decision"], "allow_task")

            controller._poll()
            self.assertIsNone(controller.active_dialog)
            self.assertEqual(ipc.consume(), [])

    def test_closing_dialog_rejects_and_new_id_can_open(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir))
            ipc.publish({"pending_approval": self._approval("approval-1")}, [])
            controller = OfficeApprovalController(ipc=ipc)
            self.addCleanup(controller.cleanup)
            controller.start()

            first = controller.active_dialog
            self.assertIsNotNone(first)
            first.close()
            self.app.processEvents()
            command = ipc.consume()[0]
            self.assertEqual(command["data"]["decision"], "reject")

            ipc.publish({"pending_approval": self._approval("approval-2")}, [])
            controller._poll()
            self.assertIsNotNone(controller.active_dialog)
            self.assertEqual(controller.active_dialog.approval_id, "approval-2")

    def test_dialog_uses_shared_pet_office_visual_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir))
            ipc.publish({"pending_approval": self._approval("approval-1")}, [])
            controller = OfficeApprovalController(ipc=ipc)
            self.addCleanup(controller.cleanup)
            controller.start()
            dialog = controller.active_dialog

            self.assertEqual(dialog.styleSheet(), office_stylesheet())
            self.assertIn(get_workbench_colors().pink, dialog.styleSheet())
            self.assertIn(get_workbench_colors().cyan, dialog.styleSheet())
            self.assertIsNotNone(dialog.findChild(QWidget, "OfficeAccentBar"))
            self.assertIsNotNone(
                dialog.findChild(QPlainTextEdit, "OfficeApprovalCommand")
            )
            scope = dialog.findChild(QLabel, "OfficeApprovalScope")
            self.assertIn("当前任务", scope.text())


if __name__ == "__main__":
    unittest.main()
