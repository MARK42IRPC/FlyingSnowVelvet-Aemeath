"""Qt workbench surface for DeepSeek Harness office tasks."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.workbench_page import QtWorkbenchToolPage
from lib.script.office.contracts import ACTIVE_TASK_STATUSES
from lib.script.office.ipc import OfficeFileIpc
from lib.script.office.workspace import DEFAULT_WORKSPACE_NAME, resolve_desktop_dir
from lib.script.ui.office_chat_view import OfficeConversationView
from lib.script.ui.office_icons import (
    office_browse_icon,
    office_cancel_icon,
    office_delete_icon,
    office_new_icon,
    office_submit_icon,
)
from lib.script.ui.office_style import office_stylesheet
from lib.script.ui.workbench_settings_layout import SettingsPageHeader, SettingsSection
from lib.script.workbench.theme import get_workbench_colors


logger = get_logger(__name__)


_STATUS_TEXT = {
    "queued": "排队中",
    "running": "执行中",
    "waiting_approval": "等待许可",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}
_STATUS_TONE = {
    "queued": "info",
    "running": "active",
    "waiting_approval": "warning",
    "completed": "success",
    "failed": "danger",
    "cancelled": "muted",
}
_EFFORTS = (("off", "关闭"), ("high", "高"), ("max", "最大"))


def _display_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return text[:16]


class OfficeWorkbenchPage(QtWorkbenchToolPage):
    POLL_INTERVAL_MS = 250

    def __init__(
        self,
        *,
        embedded: bool = False,
        ipc: OfficeFileIpc | None = None,
    ) -> None:
        super().__init__(embedded=embedded)
        self.setObjectName("OfficeWorkbenchPage")
        self.setMinimumSize(scale_px(720, min_abs=660), scale_px(520, min_abs=470))

        self._ipc = ipc or OfficeFileIpc()
        self._state: dict = {}
        self._tasks: list[dict] = []
        self._selected_task_id: str | None = None
        self._new_task_draft = False
        self._seen_new_task_revision: int | None = None
        self._submission_pending = False
        self._updating_controls = False

        self._build_ui()
        self._apply_theme()
        self.set_embedded_mode(embedded)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_state)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(12, min_abs=9),
            scale_px(10, min_abs=8),
            scale_px(12, min_abs=9),
            scale_px(12, min_abs=9),
        )
        root.setSpacing(scale_px(7, min_abs=5))

        self._page_header = SettingsPageHeader(
            "办公任务",
            "创建并跟踪桌面办公任务，查看执行详情与工具记录。",
            self,
        )
        root.addWidget(self._page_header)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setObjectName("OfficeMainSplitter")
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        history_card = SettingsSection("任务历史", "", splitter)
        history_card.setMinimumWidth(scale_px(210, min_abs=190))
        history_card.setMaximumWidth(scale_px(310, min_abs=280))
        history_layout = history_card.body_layout
        history_layout.setSpacing(scale_px(8, min_abs=6))

        history_header = QHBoxLayout()
        history_header.addStretch(1)
        self._new_task_button = QToolButton(history_card)
        self._new_task_button.setObjectName("OfficeNewTaskButton")
        self._new_task_button.setText("新任务")
        self._new_task_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._new_task_button.setToolTip("新建办公任务")
        self._new_task_button.clicked.connect(self._start_new_task_draft)
        history_header.addWidget(self._new_task_button)
        self._delete_task_button = QToolButton(history_card)
        self._delete_task_button.setObjectName("OfficeDeleteTaskButton")
        self._delete_task_button.setToolTip("删除选中任务")
        self._delete_task_button.setAccessibleName("删除选中任务")
        self._delete_task_button.setEnabled(False)
        self._delete_task_button.clicked.connect(self._delete_selected_task)
        history_header.addWidget(self._delete_task_button)
        history_layout.addLayout(history_header)

        self._history_list = QListWidget(history_card)
        self._history_list.setObjectName("OfficeTaskHistory")
        self._history_list.setAlternatingRowColors(False)
        self._history_list.setWordWrap(True)
        self._history_list.currentItemChanged.connect(self._on_history_selection_changed)
        history_layout.addWidget(self._history_list, 1)

        content_card = SettingsSection("", "", splitter)
        content_card.title_label.setVisible(False)
        content_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout = content_card.body_layout
        content_layout.setSpacing(scale_px(9, min_abs=7))

        header = QHBoxLayout()
        title_font = get_ui_font(size=scale_px(15, min_abs=13))
        title_font.setBold(True)
        self._task_title = QLabel("新任务", content_card)
        self._task_title.setObjectName("OfficeTaskTitle")
        self._task_title.setFont(title_font)
        self._task_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header.addWidget(self._task_title, 1)
        self._status_badge = QLabel("就绪", content_card)
        self._status_badge.setObjectName("OfficeStatusBadge")
        self._status_badge.setAlignment(Qt.AlignCenter)
        header.addWidget(self._status_badge)

        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        for mode, text in (("companion", "陪伴模式"), ("office", "办公模式")):
            button = QPushButton(text, content_card)
            button.setObjectName("OfficeModeSegment")
            button.setProperty("officeMode", mode)
            button.setCheckable(True)
            button.setMinimumWidth(scale_px(88, min_abs=78))
            button.clicked.connect(
                lambda _checked=False, selected=mode: self._set_mode(selected)
            )
            mode_group.addButton(button)
            self._mode_buttons[mode] = button
            header.addWidget(button)
        content_layout.addLayout(header)

        self._error_label = QLabel("", content_card)
        self._error_label.setObjectName("OfficeErrorLabel")
        self._error_label.setWordWrap(True)
        self._error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._error_label.hide()
        content_layout.addWidget(self._error_label)

        controls = QHBoxLayout()
        controls.setSpacing(scale_px(7, min_abs=5))
        workspace_label = QLabel("工作目录", content_card)
        workspace_label.setObjectName("OfficeFieldLabel")
        controls.addWidget(workspace_label)
        self._workspace_edit = QLineEdit(content_card)
        self._workspace_edit.setObjectName("OfficeWorkspace")
        self._workspace_edit.setClearButtonEnabled(True)
        self._workspace_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._install_text_context_menu(self._workspace_edit)
        controls.addWidget(self._workspace_edit, 1)
        self._browse_button = QToolButton(content_card)
        self._browse_button.setObjectName("OfficeBrowseButton")
        self._browse_button.setToolTip("选择工作目录")
        self._browse_button.clicked.connect(self._choose_workspace)
        controls.addWidget(self._browse_button)
        effort_label = QLabel("推理强度", content_card)
        effort_label.setObjectName("OfficeFieldLabel")
        controls.addWidget(effort_label)
        self._effort_combo = QComboBox(content_card)
        self._effort_combo.setObjectName("OfficeReasoningEffort")
        for value, label in _EFFORTS:
            self._effort_combo.addItem(label, value)
        self._effort_combo.currentIndexChanged.connect(self._on_effort_changed)
        controls.addWidget(self._effort_combo)
        self._cancel_button = QToolButton(content_card)
        self._cancel_button.setObjectName("OfficeCancelButton")
        self._cancel_button.setText("取消任务")
        self._cancel_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._cancel_button.clicked.connect(self._cancel_active_task)
        controls.addWidget(self._cancel_button)
        content_layout.addLayout(controls)

        self._tabs = QTabWidget(content_card)
        self._tabs.setObjectName("OfficeTaskTabs")
        self._conversation_view = OfficeConversationView(self._tabs)
        self._reasoning_view = self._read_only_view("OfficeReasoning")
        self._todo_list = QListWidget(self._tabs)
        self._todo_list.setObjectName("OfficeTodoList")
        self._events_view = self._read_only_view("OfficeEvents")
        self._tabs.addTab(self._conversation_view, "对话")
        self._tabs.addTab(self._reasoning_view, "推理")
        self._tabs.addTab(self._todo_list, "待办")
        self._tabs.addTab(self._events_view, "工具记录")
        content_layout.addWidget(self._tabs, 1)

        self._prompt_edit = QPlainTextEdit(content_card)
        self._prompt_edit.setObjectName("OfficePrompt")
        self._prompt_edit.setPlaceholderText("输入任务或继续要求")
        self._prompt_edit.setMinimumHeight(scale_px(74, min_abs=66))
        self._prompt_edit.setMaximumHeight(scale_px(116, min_abs=104))
        self._install_text_context_menu(self._prompt_edit)
        content_layout.addWidget(self._prompt_edit)

        submit_row = QHBoxLayout()
        self._selection_hint = QLabel("", content_card)
        self._selection_hint.setObjectName("OfficeSelectionHint")
        submit_row.addWidget(self._selection_hint, 1)
        self._submit_button = QPushButton("开始任务", content_card)
        self._submit_button.setObjectName("OfficeSubmitButton")
        self._submit_button.setProperty("primary", True)
        self._submit_button.clicked.connect(self._submit_prompt)
        submit_row.addWidget(self._submit_button)
        content_layout.addLayout(submit_row)

        splitter.addWidget(history_card)
        splitter.addWidget(content_card)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([scale_px(245, min_abs=220), scale_px(720, min_abs=640)])

    def _read_only_view(self, object_name: str) -> QPlainTextEdit:
        view = QPlainTextEdit(self)
        view.setObjectName(object_name)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self._install_text_context_menu(view)
        return view

    def _install_text_context_menu(self, edit: QLineEdit | QPlainTextEdit) -> None:
        edit.setContextMenuPolicy(Qt.CustomContextMenu)
        edit.customContextMenuRequested.connect(
            lambda pos, target=edit: self._show_text_context_menu(target, pos)
        )

    @staticmethod
    def _show_text_context_menu(edit: QLineEdit | QPlainTextEdit, pos) -> None:
        menu = QMenu(edit)
        action_copy = menu.addAction("复制")
        action_paste = menu.addAction("粘贴")
        action_cut = menu.addAction("剪切")

        can_edit = not bool(edit.isReadOnly())
        has_selection = (
            bool(edit.textCursor().hasSelection())
            if isinstance(edit, QPlainTextEdit)
            else bool(edit.hasSelectedText())
        )
        can_paste = can_edit and bool(QApplication.clipboard().text())
        action_copy.setEnabled(has_selection)
        action_paste.setEnabled(can_paste)
        action_cut.setEnabled(can_edit and has_selection)

        chosen = menu.exec_(edit.mapToGlobal(pos))
        if chosen is action_copy:
            edit.copy()
        elif chosen is action_paste:
            edit.paste()
        elif chosen is action_cut:
            edit.cut()

    def refresh_workbench_page(self) -> None:
        self._poll_state(force=True)
        if self.isVisible() and not self._poll_timer.isActive():
            self._poll_timer.start()

    def refresh_workbench_theme(self) -> None:
        self._apply_theme()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._poll_state(force=True)
        self._poll_timer.start()
        # 预热办公运行时，减少首次任务的等待时间
        try:
            from lib.script.office.service import get_office_service
            get_office_service().warmup_runtime()
        except Exception:
            pass

    def hideEvent(self, event) -> None:
        self._poll_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        super().closeEvent(event)

    def _poll_state(self, force: bool = False) -> None:
        state = self._ipc.read_state()
        tasks = [task for task in self._ipc.read_tasks() if isinstance(task, dict)]
        try:
            new_task_revision = int(state.get("new_task_revision") or 0)
        except (TypeError, ValueError):
            new_task_revision = 0
        revision_changed = self._seen_new_task_revision != new_task_revision
        self._seen_new_task_revision = new_task_revision
        changed = force or state != self._state or tasks != self._tasks
        if changed:
            self._state = state
            self._tasks = tasks
            self._submission_pending = False
            self._select_initial_task()
            self._refresh_task_list()
            self._refresh_selected_task()
        if revision_changed and new_task_revision > 0 and self._active_task() is None:
            self._start_new_task_draft()
        self._sync_mode_buttons()

    def _active_task(self) -> dict | None:
        active_id = str(self._state.get("active_task_id") or "")
        if active_id:
            task = self._task_by_id(active_id)
            if task is not None:
                return task
        return next(
            (task for task in reversed(self._tasks) if str(task.get("status")) in ACTIVE_TASK_STATUSES),
            None,
        )

    def _task_by_id(self, task_id: str | None) -> dict | None:
        value = str(task_id or "")
        return next((task for task in self._tasks if str(task.get("id")) == value), None)

    def _select_initial_task(self) -> None:
        active = self._active_task()
        if active is not None:
            self._new_task_draft = False
            self._selected_task_id = str(active.get("id"))
            return
        if self._new_task_draft:
            return
        if self._task_by_id(self._selected_task_id) is not None:
            return
        if self._tasks:
            self._selected_task_id = str(self._tasks[-1].get("id"))
        else:
            self._selected_task_id = None

    def _refresh_task_list(self) -> None:
        blocker = QSignalBlocker(self._history_list)
        self._history_list.clear()
        selected_item = None
        for task in reversed(self._tasks):
            task_id = str(task.get("id") or "")
            status = str(task.get("status") or "")
            title = str(task.get("title") or "未命名任务")
            subtitle = "  ".join(
                part for part in (_STATUS_TEXT.get(status, status), _display_time(task.get("updated_at"))) if part
            )
            item = QListWidgetItem(f"{title}\n{subtitle}", self._history_list)
            item.setData(Qt.UserRole, task_id)
            item.setToolTip(str(task.get("workspace") or ""))
            if task_id == self._selected_task_id and not self._new_task_draft:
                selected_item = item
        if selected_item is not None:
            self._history_list.setCurrentItem(selected_item)
        else:
            self._history_list.clearSelection()
            self._history_list.setCurrentItem(None)
        del blocker

    def _on_history_selection_changed(self, current, previous) -> None:
        del previous
        if current is None:
            return
        self._new_task_draft = False
        self._selected_task_id = str(current.data(Qt.UserRole) or "")
        self._refresh_selected_task()

    def _start_new_task_draft(self) -> None:
        if self._active_task() is not None:
            return
        self._new_task_draft = True
        self._selected_task_id = None
        self._history_list.clearSelection()
        self._history_list.setCurrentItem(None)
        self._prompt_edit.clear()
        self._refresh_selected_task()
        self._prompt_edit.setFocus()

    def _refresh_selected_task(self) -> None:
        task = None if self._new_task_draft else self._task_by_id(self._selected_task_id)
        active = self._active_task()
        self._updating_controls = True
        try:
            if task is None:
                self._task_title.setText("新任务")
                self._set_status("ready", "就绪")
                default_workspace = str(
                    self._state.get("workspace")
                    or (resolve_desktop_dir() / DEFAULT_WORKSPACE_NAME)
                )
                if not self._workspace_edit.text() or not self._workspace_edit.hasFocus():
                    self._workspace_edit.setText(default_workspace)
                    self._workspace_edit.setCursorPosition(0)
                self._workspace_edit.setReadOnly(False)
                self._browse_button.setEnabled(True)
                self._set_effort("high")
                self._conversation_view.clear()
                self._set_plain_text(self._reasoning_view, "")
                self._set_plain_text(self._events_view, "")
                self._todo_list.clear()
                self._selection_hint.setText("默认目录：桌面 / 飞行雪绒办公区")
                can_submit = active is None
                self._submit_button.setText("开始任务")
            else:
                status = str(task.get("status") or "")
                self._task_title.setText(str(task.get("title") or "未命名任务"))
                self._set_status(status, _STATUS_TEXT.get(status, status or "未知"))
                self._workspace_edit.setText(str(task.get("workspace") or ""))
                self._workspace_edit.setReadOnly(True)
                self._workspace_edit.setCursorPosition(0)
                self._browse_button.setEnabled(False)
                self._set_effort(str(task.get("reasoning_effort") or "high"))
                self._render_task(task)
                can_resume = bool(str(task.get("session_id") or ""))
                can_submit = active is None and can_resume
                self._submit_button.setText("继续任务")
                self._selection_hint.setText(
                    _STATUS_TEXT.get(status, status) + "  " + _display_time(task.get("updated_at"))
                )
            self._new_task_button.setEnabled(active is None and not self._submission_pending)
            self._submit_button.setEnabled(can_submit and not self._submission_pending)
            self._cancel_button.setEnabled(active is not None)
            self._delete_task_button.setEnabled(
                task is not None
                and str(task.get("status") or "") not in ACTIVE_TASK_STATUSES
                and not self._submission_pending
            )
            error = str((task or {}).get("error") or self._state.get("runtime_error") or "").strip()
            self._error_label.setText(error)
            self._error_label.setVisible(bool(error))
        finally:
            self._updating_controls = False

    def _render_task(self, task: dict) -> None:
        messages: list[tuple[str, str, bool, str]] = []
        for message in task.get("messages", []):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "system")
            text = str(message.get("text") or "").strip()
            if text:
                messages.append((role, text, False, _display_time(message.get("time"))))
        stream_text = str(task.get("stream_text") or "").strip()
        if stream_text:
            messages.append(("assistant", stream_text, True, ""))
        self._conversation_view.set_messages(messages)
        self._set_plain_text(self._reasoning_view, str(task.get("reasoning_text") or ""))

        self._todo_list.clear()
        for todo in task.get("todos", []):
            if isinstance(todo, dict):
                status = str(todo.get("status") or "pending")
                text = str(todo.get("content") or todo.get("text") or todo.get("title") or "")
            else:
                status = "pending"
                text = str(todo or "")
            marker = {"completed": "[x]", "in_progress": "[~]"}.get(status, "[ ]")
            if text.strip():
                self._todo_list.addItem(f"{marker} {text.strip()}")

        event_lines = []
        for item in task.get("events", [])[-100:]:
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("type") or "event")
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            event_lines.append(
                f"{_display_time(item.get('time'))}  {event_type}\n"
                f"{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
            )
        self._set_plain_text(self._events_view, "\n\n".join(event_lines))

    @staticmethod
    def _set_plain_text(view: QPlainTextEdit, text: str) -> None:
        normalized = str(text or "")
        if view.toPlainText() == normalized:
            return
        scrollbar = view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        view.setPlainText(normalized)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _set_status(self, status: str, text: str) -> None:
        self._status_badge.setText(text)
        self._status_badge.setProperty("tone", _STATUS_TONE.get(status, "muted"))
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)

    def _set_effort(self, effort: str) -> None:
        index = self._effort_combo.findData(effort)
        self._effort_combo.setCurrentIndex(index if index >= 0 else 1)

    def _sync_mode_buttons(self) -> None:
        mode = "office" if str(self._state.get("mode")) == "office" else "companion"
        self._mode_buttons[mode].setChecked(True)

    def _set_mode(self, mode: str) -> None:
        self._ipc.submit("set_mode", mode=mode)

    def _choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择工作目录",
            self._workspace_edit.text().strip() or str(resolve_desktop_dir()),
        )
        if selected:
            self._workspace_edit.setText(str(Path(selected).resolve()))

    def _on_effort_changed(self, index: int) -> None:
        if self._updating_controls:
            return
        task = self._task_by_id(self._selected_task_id)
        if task is None:
            return
        effort = str(self._effort_combo.itemData(index) or "high")
        self._ipc.submit(
            "set_reasoning",
            task_id=str(task.get("id") or ""),
            reasoning_effort=effort,
        )

    def _submit_prompt(self) -> None:
        if self._active_task() is not None:
            return
        text = self._prompt_edit.toPlainText().strip()
        if not text or self._submission_pending:
            return
        task = None if self._new_task_draft else self._task_by_id(self._selected_task_id)
        if task is None:
            workspace = self._workspace_edit.text().strip()
            if not workspace:
                return
            self._ipc.submit(
                "new_task",
                text=text,
                workspace=workspace,
                reasoning_effort=str(self._effort_combo.currentData() or "high"),
            )
        else:
            self._ipc.submit(
                "followup",
                task_id=str(task.get("id") or ""),
                text=text,
            )
        self._submission_pending = True
        self._prompt_edit.clear()
        self._refresh_selected_task()

    def _cancel_active_task(self) -> None:
        active = self._active_task()
        if active is not None:
            self._ipc.submit("cancel", task_id=str(active.get("id") or ""))

    def _delete_selected_task(self) -> None:
        task = self._task_by_id(self._selected_task_id)
        pid = os.getpid()
        ipc_root = str(self._ipc.root)
        if task is None:
            logger.warning(
                "[OfficePage] 删除跳过：未找到选中任务 selected=%r pid=%s ipc_root=%s",
                self._selected_task_id,
                pid,
                ipc_root,
            )
            return
        task_id = str(task.get("id") or "")
        logger.info(
            "[OfficePage] 删除任务入口 task_id=%s title=%r status=%r pid=%s ipc_root=%s",
            task_id,
            str(task.get("title") or ""),
            str(task.get("status") or ""),
            pid,
            ipc_root,
        )
        if str(task.get("status") or "") in ACTIVE_TASK_STATUSES:
            self._selection_hint.setText("运行中的任务请先取消")
            logger.info(
                "[OfficePage] 删除被拒：任务运行中 task_id=%s status=%r",
                task_id,
                str(task.get("status") or ""),
            )
            return

        title = str(task.get("title") or "未命名任务")
        workspace = str(task.get("workspace") or "")
        dialog = QMessageBox(self)
        dialog.setObjectName("OfficeConfirmDialog")
        dialog.setWindowTitle("删除办公任务")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"确定删除任务“{title}”？")
        if workspace:
            dialog.setInformativeText(f"工作目录：{workspace}")
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm_button = dialog.button(QMessageBox.Yes)
        if confirm_button is not None:
            confirm_button.setText("删除")
            confirm_button.setObjectName("OfficeConfirmDelete")
        cancel_button = dialog.button(QMessageBox.No)
        if cancel_button is not None:
            cancel_button.setText("取消")
            cancel_button.setObjectName("OfficeConfirmCancel")
        dialog.setDefaultButton(QMessageBox.No)
        dialog.setStyleSheet(office_stylesheet())
        if dialog.exec_() != QMessageBox.Yes:
            logger.info("[OfficePage] 删除被取消：弹窗未确认 task_id=%s", task_id)
            return
        logger.info("[OfficePage] 弹窗确认，提交删除命令 task_id=%s pid=%s ipc_root=%s", task_id, pid, ipc_root)
        self._ipc.submit("delete", task_id=task_id)
        self._submission_pending = True
        self._delete_task_button.setEnabled(False)

    def _apply_theme(self) -> None:
        self.setStyleSheet(office_stylesheet())
        self._conversation_view.refresh()
        self._refresh_office_icons()

    def _refresh_office_icons(self) -> None:
        colors = get_workbench_colors()
        icon_color = colors.text
        icon_size = QSize(scale_px(16, min_abs=14), scale_px(16, min_abs=14))
        self._new_task_button.setIcon(office_new_icon(icon_color))
        self._new_task_button.setIconSize(icon_size)
        self._delete_task_button.setIcon(office_delete_icon(icon_color))
        self._delete_task_button.setIconSize(icon_size)
        self._browse_button.setIcon(office_browse_icon(icon_color))
        self._browse_button.setIconSize(icon_size)
        self._cancel_button.setIcon(office_cancel_icon(icon_color))
        self._cancel_button.setIconSize(icon_size)
        self._submit_button.setIcon(office_submit_icon(colors.canvas))
