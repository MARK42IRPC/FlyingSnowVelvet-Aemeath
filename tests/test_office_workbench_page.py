from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="office-workbench-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QMessageBox, QWidget

from lib.script.office.ipc import OfficeFileIpc
from lib.script.ui.office_page import OfficeWorkbenchPage
from lib.script.ui.office_style import office_stylesheet
from lib.script.workbench.theme import get_workbench_colors


class OfficeWorkbenchPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, root: Path) -> tuple[OfficeWorkbenchPage, OfficeFileIpc]:
        ipc = OfficeFileIpc(root)
        page = OfficeWorkbenchPage(embedded=True, ipc=ipc)
        self.addCleanup(page.close)
        return page, ipc

    def test_new_task_uses_workspace_and_reasoning_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            page, ipc = self._page(root / "ipc")
            ipc.publish(
                {
                    "mode": "companion",
                    "workspace": str(workspace),
                    "active_task_id": None,
                },
                [],
            )
            page.refresh_workbench_page()
            page._start_new_task_draft()
            page._workspace_edit.setText(str(workspace))
            page._effort_combo.setCurrentIndex(page._effort_combo.findData("max"))
            page._prompt_edit.setPlainText("创建一个项目")

            page._submit_prompt()
            commands = ipc.consume()

            self.assertEqual(commands[0]["command"], "new_task")
            self.assertEqual(commands[0]["data"]["workspace"], str(workspace))
            self.assertEqual(commands[0]["data"]["reasoning_effort"], "max")

    def test_active_task_disables_new_task_and_submits_followup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "task-1",
                "session_id": "session-1",
                "title": "当前任务",
                "workspace": str(root / "workspace"),
                "status": "running",
                "reasoning_effort": "high",
                "updated_at": "2026-08-17T12:00:00+00:00",
                "messages": [{"role": "assistant", "text": "处理中", "time": ""}],
                "events": [],
                "todos": [],
                "stream_text": "正在生成",
                "reasoning_text": "分析中",
                "error": "",
            }
            page, ipc = self._page(root / "ipc")
            ipc.publish(
                {
                    "mode": "office",
                    "workspace": task["workspace"],
                    "active_task_id": task["id"],
                },
                [task],
            )
            page.refresh_workbench_page()

            self.assertFalse(page._new_task_button.isEnabled())
            self.assertIn("正在生成", page._conversation_view.toPlainText())
            self.assertEqual(page._reasoning_view.toPlainText(), "分析中")

            page._prompt_edit.setPlainText("继续")
            page._submit_prompt()
            commands = ipc.consume()

            self.assertEqual(commands[0]["command"], "followup")
            self.assertEqual(commands[0]["data"]["task_id"], "task-1")

    def test_page_uses_shared_pet_office_visual_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")

            self.assertEqual(page.styleSheet(), office_stylesheet())
            self.assertIn(get_workbench_colors().pink, page.styleSheet())
            self.assertIn(get_workbench_colors().cyan, page.styleSheet())
            self.assertIsNone(page.findChild(QWidget, "OfficeAccentBar"))
            self.assertIsNotNone(page.findChild(QFrame, "SettingsPageHeader"))
            self.assertEqual(len(page.findChildren(QWidget, "SettingsSection")), 2)
            section_titles = [
                label.text()
                for label in page.findChildren(QLabel, "SettingsSectionTitle")
            ]
            self.assertEqual(
                [title for title in section_titles if title],
                ["任务历史"],
            )
            self.assertEqual(
                page._mode_buttons["companion"].property("officeMode"),
                "companion",
            )
            self.assertEqual(page._mode_buttons["office"].property("officeMode"), "office")

    def test_new_revision_switches_page_to_a_blank_draft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "task-1",
                "session_id": "session-1",
                "title": "旧任务",
                "workspace": str(root / "workspace"),
                "status": "completed",
                "reasoning_effort": "high",
                "updated_at": "2026-08-17T12:00:00+00:00",
                "messages": [],
                "events": [],
                "todos": [],
                "stream_text": "",
                "reasoning_text": "",
                "error": "",
            }
            page, ipc = self._page(root / "ipc")
            ipc.publish(
                {
                    "mode": "office",
                    "workspace": task["workspace"],
                    "active_task_id": None,
                    "new_task_revision": 1,
                },
                [task],
            )

            page.refresh_workbench_page()

            self.assertTrue(page._new_task_draft)
            self.assertEqual(page._task_title.text(), "新任务")
            self.assertEqual(page._selected_task_id, None)

    def test_delete_button_submits_confirmed_inactive_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "task-1",
                "session_id": "session-1",
                "title": "待删除任务",
                "workspace": str(root / "workspace"),
                "status": "completed",
                "reasoning_effort": "high",
                "updated_at": "2026-08-17T12:00:00+00:00",
                "messages": [],
                "events": [],
                "todos": [],
                "stream_text": "",
                "reasoning_text": "",
                "error": "",
            }
            page, ipc = self._page(root / "ipc")
            ipc.publish(
                {
                    "mode": "office",
                    "workspace": task["workspace"],
                    "active_task_id": None,
                },
                [task],
            )
            page.refresh_workbench_page()

            self.assertTrue(page._delete_task_button.isEnabled())
            with patch("lib.script.ui.office_page.QMessageBox.exec_", return_value=QMessageBox.Yes):
                page._delete_selected_task()

            commands = ipc.consume()
            self.assertEqual(commands[0]["command"], "delete")
            self.assertEqual(commands[0]["data"]["task_id"], "task-1")

if __name__ == "__main__":
    unittest.main()
