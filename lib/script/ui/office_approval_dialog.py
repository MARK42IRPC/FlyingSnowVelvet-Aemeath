"""Qt permission decision dialog for office tasks."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.qt_bridge.font import get_ui_font
from lib.script.ui.office_icons import (
    office_allow_icon,
    office_allow_task_icon,
    office_reject_icon,
    office_warning_icon,
)
from lib.script.ui.office_style import create_office_accent_bar, office_stylesheet
from lib.script.workbench.theme import get_workbench_colors


_OFFICE_ICON_PATH = Path(__file__).resolve().parents[3] / "resc" / "icon.ico"


class OfficeApprovalDialog(QDialog):
    decision_made = pyqtSignal(str, str)

    def __init__(self, approval: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._approval = dict(approval or {})
        self._approval_id = str(self._approval.get("approval_id", ""))
        self._resolved = False

        self.setObjectName("OfficeApprovalDialog")
        self.setWindowTitle("办公权限许可")
        self.setWindowModality(Qt.WindowModal if parent is not None else Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(scale_px(500, min_abs=460))
        self.setMaximumWidth(scale_px(680, min_abs=620))
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        if _OFFICE_ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(_OFFICE_ICON_PATH)))

        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(22, min_abs=18),
            scale_px(20, min_abs=16),
            scale_px(22, min_abs=18),
            scale_px(18, min_abs=15),
        )
        root.setSpacing(scale_px(12, min_abs=9))

        self._accent_bar = create_office_accent_bar(self)
        root.addWidget(self._accent_bar)

        header = QFrame(self)
        header.setObjectName("OfficeApprovalHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(10, min_abs=8),
            scale_px(12, min_abs=10),
            scale_px(10, min_abs=8),
        )
        header_layout.setSpacing(scale_px(10, min_abs=8))
        icon_label = QLabel(header)
        icon_label.setObjectName("OfficeApprovalIcon")
        icon_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._icon_label = icon_label
        header_layout.addWidget(icon_label)

        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(scale_px(3, min_abs=2))
        kicker = QLabel("办公模式 · 权限许可", header)
        kicker.setObjectName("OfficeApprovalKicker")
        kicker.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        title_column.addWidget(kicker)
        title = QLabel(str(self._approval.get("tool_name") or "执行受限操作"), header)
        title.setObjectName("OfficeApprovalTitle")
        title_font = get_ui_font(size=scale_px(15, min_abs=13))
        title_font.setBold(True)
        title.setFont(title_font)
        title.setWordWrap(True)
        title_column.addWidget(title)
        header_layout.addLayout(title_column, 1)
        root.addWidget(header)

        reason = str(self._approval.get("reason") or "需要用户许可")
        reason_label = QLabel(reason, self)
        reason_label.setObjectName("OfficeApprovalReason")
        reason_label.setFont(get_ui_font(size=scale_px(11, min_abs=10)))
        reason_label.setWordWrap(True)
        reason_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(reason_label)

        command = self._approval.get("command")
        if command:
            command_label = QLabel("请求内容", self)
            command_label.setObjectName("OfficeApprovalCommandLabel")
            root.addWidget(command_label)
            self._command_view = QPlainTextEdit(self)
            self._command_view.setObjectName("OfficeApprovalCommand")
            self._command_view.setReadOnly(True)
            self._command_view.setMaximumHeight(scale_px(150, min_abs=120))
            self._command_view.setPlainText(json.dumps(command, ensure_ascii=False, indent=2))
            self._command_view.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
            root.addWidget(self._command_view)
        else:
            self._command_view = None

        scope_label = QLabel("“始终允许”仅对当前任务有效，任务结束后自动失效。", self)
        scope_label.setObjectName("OfficeApprovalScope")
        scope_label.setWordWrap(True)
        root.addWidget(scope_label)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, scale_px(4, min_abs=3), 0, 0)
        actions.setSpacing(scale_px(8, min_abs=6))
        reject_button = QPushButton("拒绝", self)
        reject_button.setObjectName("OfficeApprovalReject")
        reject_button.clicked.connect(lambda: self._resolve("reject"))
        actions.addWidget(reject_button)

        actions.addStretch(1)

        allow_button = QPushButton("允许", self)
        allow_button.setObjectName("OfficeApprovalAllow")
        allow_button.clicked.connect(lambda: self._resolve("allow"))
        actions.addWidget(allow_button)

        allow_task_button = QPushButton("始终允许", self)
        allow_task_button.setObjectName("OfficeApprovalAllowTask")
        allow_task_button.clicked.connect(lambda: self._resolve("allow_task"))
        actions.addWidget(allow_task_button)

        root.addLayout(actions)

        self._apply_theme()

    @property
    def approval_id(self) -> str:
        return self._approval_id

    def _resolve(self, decision: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.decision_made.emit(self._approval_id, decision)
        self.accept()

    def dismiss_without_decision(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.reject()

    def closeEvent(self, event) -> None:
        if not self._resolved:
            self._resolved = True
            self.decision_made.emit(self._approval_id, "reject")
        super().closeEvent(event)

    def _apply_theme(self) -> None:
        self.setStyleSheet(office_stylesheet())
        colors = get_workbench_colors()
        icon_size = scale_px(24, min_abs=21)
        self._icon_label.setPixmap(
            office_warning_icon(colors.warning).pixmap(icon_size, icon_size)
        )
        self.findChild(QPushButton, "OfficeApprovalReject").setIcon(
            office_reject_icon(colors.text)
        )
        self.findChild(QPushButton, "OfficeApprovalAllow").setIcon(
            office_allow_icon(colors.canvas)
        )
        self.findChild(QPushButton, "OfficeApprovalAllowTask").setIcon(
            office_allow_task_icon(colors.canvas)
        )
