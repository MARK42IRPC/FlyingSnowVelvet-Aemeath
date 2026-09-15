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
from PyQt5.QtCore import QPoint

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMenu,
    QMessageBox,
    QToolButton,
    QWidget,
)

from lib.script.office.ipc import OfficeFileIpc
from lib.script.ui.office_page import OfficeWorkbenchPage
from lib.script.ui.office_style import office_stylesheet
from lib.script.ui.workbench_settings_layout import (
    SETTINGS_FONT_SIZE,
    SETTINGS_HINT_FONT_SIZE,
)
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
            page._effort_slider.set_effort("max")
            page._prompt_edit.setPlainText("创建一个项目")

            page._submit_prompt()
            commands = ipc.consume()

            self.assertEqual(commands[0]["command"], "new_task")
            self.assertEqual(commands[0]["data"]["workspace"], str(workspace))
            self.assertEqual(commands[0]["data"]["reasoning_effort"], "max")

    def test_effort_slider_uses_five_levels_and_sends_wire_value(self):
        from lib.script.office.contracts import REASONING_EFFORTS
        from lib.script.ui.office_effort_slider import EFFORT_LEVELS, EFFORT_TICK_COUNT

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            page, ipc = self._page(root / "ipc")
            slider = page._effort_slider

            self.assertEqual(
                [value for value, _label in EFFORT_LEVELS], list(REASONING_EFFORTS)
            )
            self.assertEqual(slider._slider.minimum(), 0)
            self.assertEqual(slider._slider.maximum(), 4)
            self.assertEqual(slider._slider.singleStep(), 1)
            self.assertEqual(slider._slider.pageStep(), 1)
            # QSS 覆盖 groove/handle 后原生刻度不再绘制，五档刻度由滑条自绘。
            self.assertEqual(slider._slider.tickPosition(), slider._slider.NoTicks)
            self.assertEqual(EFFORT_TICK_COUNT, 5)

            slider._slider.setValue(4)
            self.assertEqual(slider.effort(), "ultra")
            self.assertEqual(slider._level_label.text(), "沉思")

            slider.set_effort("low")
            self.assertEqual(slider.effort(), "low")
            self.assertEqual(slider._level_label.text(), "轻量")
            slider.set_effort("不存在的档位")
            self.assertEqual(slider.effort(), "high")

    def test_selected_task_effort_change_sends_set_reasoning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "task-1",
                "session_id": "session-1",
                "title": "进行中",
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
            ipc.publish({"mode": "office", "workspace": task["workspace"], "active_task_id": None}, [task])
            page.refresh_workbench_page()
            page._history_list.setCurrentRow(0)
            ipc.consume()

            page._effort_slider._slider.setValue(3)

            commands = ipc.consume()
            self.assertEqual(commands[0]["command"], "set_reasoning")
            self.assertEqual(commands[0]["data"]["task_id"], "task-1")
            self.assertEqual(commands[0]["data"]["reasoning_effort"], "max")

    def test_active_task_disables_new_task_and_submit(self):
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
            self.assertFalse(page._submit_button.isEnabled())
            page._submit_prompt()
            commands = ipc.consume()

            self.assertEqual(commands, [])

    def test_streaming_message_updates_existing_bubble(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = {
                "id": "task-1",
                "session_id": "session-1",
                "title": "流式任务",
                "workspace": str(root / "workspace"),
                "status": "running",
                "reasoning_effort": "high",
                "updated_at": "2026-08-17T12:00:00+00:00",
                "messages": [{"role": "assistant", "text": "第一段", "time": ""}],
                "events": [],
                "todos": [],
                "stream_text": "正在生成",
                "reasoning_text": "",
                "error": "",
            }
            page, ipc = self._page(root / "ipc")
            ipc.publish({"mode": "office", "active_task_id": "task-1"}, [task])
            page.refresh_workbench_page()
            row = page._conversation_view._rows[-1]

            task["stream_text"] = "正在生成更多内容"
            ipc.publish({"mode": "office", "active_task_id": "task-1"}, [task])
            page.refresh_workbench_page()

            self.assertIs(page._conversation_view._rows[-1], row)
            self.assertIn("正在生成更多内容", page._conversation_view.toPlainText())

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

    def test_reasoning_slider_sits_left_of_the_submit_button_in_the_composer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")
            page.resize(1120, 760)
            page.show()
            self.app.processEvents()

            composer = page.findChild(QFrame, "OfficeComposer")
            self.assertIsNotNone(composer)
            # 推理滑条与发送按钮同处底部输入坞，滑条在按钮左侧、同一行。
            self.assertIs(page._effort_slider.parent(), composer)
            self.assertIs(page._submit_button.parent(), composer)
            slider_center = page._effort_slider.mapTo(page, page._effort_slider.rect().center())
            submit_center = page._submit_button.mapTo(page, page._submit_button.rect().center())
            self.assertLess(slider_center.x(), submit_center.x())
            self.assertLessEqual(abs(slider_center.y() - submit_center.y()), 24)
            # 提示词也在同一块输入坞里，且排在动作条上面。
            self.assertIs(page._prompt_edit.parent(), composer)
            prompt_bottom = page._prompt_edit.mapTo(page, page._prompt_edit.rect().bottomLeft()).y()
            self.assertLess(prompt_bottom, slider_center.y())

    def test_embedded_page_has_no_window_buttons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")

            self.assertEqual(page.findChildren(QToolButton, "WorkbenchWindowButton"), [])

    def test_standalone_window_offers_minimize_fullscreen_and_close(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir) / "ipc")
            page = OfficeWorkbenchPage(embedded=False, ipc=ipc)
            self.addCleanup(page.deleteLater)

            buttons = page.findChildren(QToolButton, "WorkbenchWindowButton")
            self.assertEqual(len(buttons), 3)
            self.assertEqual(page._minimize_button.toolTip(), "最小化")
            self.assertEqual(page._fullscreen_button.toolTip(), "全屏")
            self.assertEqual(page._close_button.toolTip(), "关闭办公页面")
            self.assertTrue(page._close_button.property("danger"))
            self.assertTrue(page._size_grip.isVisibleTo(page))

            page._toggle_fullscreen()
            self.assertTrue(page.isFullScreen())
            self.assertEqual(page._fullscreen_button.toolTip(), "退出全屏")
            self.assertFalse(page._size_grip.isVisibleTo(page))

            page._toggle_fullscreen()
            self.assertFalse(page.isFullScreen())
            self.assertEqual(page._fullscreen_button.toolTip(), "全屏")
            self.assertTrue(page._size_grip.isVisibleTo(page))

    def test_size_grip_sticks_to_the_bottom_right_corner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ipc = OfficeFileIpc(Path(tmpdir) / "ipc")
            page = OfficeWorkbenchPage(embedded=False, ipc=ipc)
            self.addCleanup(page.deleteLater)
            page.resize(1120, 760)
            page.show()
            self.app.processEvents()

            grip = page._size_grip
            # 手柄建出来时还没显示，只按 resizeEvent 摆位会把它留在 (0, 0)，
            # 独立窗口左上角就多出一块方点；显示时也要重新贴到右下角。
            self.assertEqual(grip.x(), page.width() - grip.width())
            self.assertEqual(grip.y(), page.height() - grip.height())

    def test_holding_the_effort_slider_streams_star_particles_each_tick(self):
        from lib.core.event.center import Event, EventType, get_event_center
        from lib.script.ui.office_page import EFFORT_STAR_PARTICLE_ID

        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")
            page.resize(1120, 760)
            page.show()
            self.app.processEvents()

            center = get_event_center()
            received: list = []
            center.subscribe(EventType.PARTICLE_REQUEST, received.append)
            self.addCleanup(center.unsubscribe, EventType.PARTICLE_REQUEST, received.append)

            page._effort_slider.set_effort("ultra")
            page._begin_effort_star_trail()
            center.publish(Event(EventType.TICK))
            center.publish(Event(EventType.TICK))
            handle = page._effort_slider.handle_center()
            page._effort_slider.set_effort("off")
            center.publish(Event(EventType.TICK))
            page._end_effort_star_trail()
            center.publish(Event(EventType.TICK))

            # 按下时先召唤一批，之后每个逻辑 tick 一批；松开后不再召唤。
            self.assertEqual(len(received), 4)
            self.assertEqual(
                [event.data["particle_id"] for event in received],
                [EFFORT_STAR_PARTICLE_ID] * 4,
            )
            self.assertTrue(all(event.data["area_type"] == "point" for event in received))
            first = received[0].data["area_data"]
            self.assertEqual(first, (handle.x(), handle.y()))
            self.assertLess(received[-1].data["area_data"][0], first[0])
            # 光点从滑块把手往外飘：粒子自己带默认参数，调用方不重复给。
            self.assertTrue(all(not event.data.get("particle_options") for event in received))

    def test_office_text_follows_workbench_settings_font_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")

            # 正文与控件字号直接取工作台设置页档位，不再吃应用默认的 12px。
            self.assertEqual(page._task_title.font().pixelSize(), SETTINGS_FONT_SIZE)
            stylesheet = page.styleSheet()
            self.assertIn(f"font-size: {SETTINGS_FONT_SIZE}px;", stylesheet)
            # 次要信息（选中提示等）比正文小两号，且不再逐条写死字号。
            self.assertIn(f"font-size: {SETTINGS_HINT_FONT_SIZE}px;", stylesheet)
            self.assertNotIn("font-size: 11px;", stylesheet)
            self.assertNotIn("font-size: 12px;", stylesheet)

            page._conversation_view.set_messages([("assistant", "你好", False, "")])
            bubble = page._conversation_view.findChild(QLabel, "OfficeChatBubbleText")
            self.assertIsNotNone(bubble)
            assert bubble is not None
            self.assertEqual(bubble.font().pixelSize(), SETTINGS_FONT_SIZE)

            # 办公面整体（列表、输入框、按钮）都铺到设置页档位：QSS 的 font-size 只管命中
            # 的那个控件本身，不铺这一遍子控件还是应用默认的 12px。
            for widget in (
                page._history_list,
                page._prompt_edit,
                page._workspace_edit,
                page._submit_button,
                page._tabs.tabBar(),
            ):
                with self.subTest(widget=widget.objectName() or type(widget).__name__):
                    self.assertEqual(widget.font().pixelSize(), SETTINGS_FONT_SIZE)
            # 次要信息走小两号的提示档。
            self.assertEqual(page._selection_hint.font().pixelSize(), SETTINGS_HINT_FONT_SIZE)
            self.assertTrue(page._submit_button.font().bold())
            self.assertTrue(page._task_title.font().bold())
            self.assertFalse(page._prompt_edit.font().bold())

            # 页头说明与分区标题复用设置页同一套映射：说明小两号，分区标题与正文同档加粗。
            header_description = page.findChild(QLabel, "SettingsPageDescription")
            self.assertIsNotNone(header_description)
            assert header_description is not None
            self.assertEqual(header_description.font().pixelSize(), SETTINGS_HINT_FONT_SIZE)
            section_title = page.findChild(QLabel, "SettingsSectionTitle")
            self.assertIsNotNone(section_title)
            assert section_title is not None
            self.assertEqual(section_title.font().pixelSize(), SETTINGS_FONT_SIZE)
            self.assertTrue(section_title.font().bold())

    def test_text_context_menu_is_chinese_and_limited_to_clipboard_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")
            page._prompt_edit.setPlainText("任务")
            page._prompt_edit.selectAll()
            self.app.clipboard().setText("粘贴内容")
            captured = {}

            def capture_menu(menu, *_args):
                captured["menu"] = menu
                return None

            with patch.object(QMenu, "exec_", capture_menu):
                page._show_text_context_menu(page._prompt_edit, QPoint(0, 0))

            actions = captured["menu"].actions()
            self.assertEqual([action.text() for action in actions], ["复制", "粘贴", "剪切"])
            self.assertTrue(all(action.isEnabled() for action in actions))

    def test_read_only_text_context_menu_disables_editing_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            page, _ipc = self._page(Path(tmpdir) / "ipc")
            page._reasoning_view.setPlainText("推理")
            page._reasoning_view.selectAll()
            self.app.clipboard().setText("粘贴内容")
            captured = {}

            def capture_menu(menu, *_args):
                captured["menu"] = menu
                return None

            with patch.object(QMenu, "exec_", capture_menu):
                page._show_text_context_menu(page._reasoning_view, QPoint(0, 0))

            actions = captured["menu"].actions()
            self.assertEqual([action.text() for action in actions], ["复制", "粘贴", "剪切"])
            self.assertTrue(actions[0].isEnabled())
            self.assertFalse(actions[1].isEnabled())
            self.assertFalse(actions[2].isEnabled())

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
