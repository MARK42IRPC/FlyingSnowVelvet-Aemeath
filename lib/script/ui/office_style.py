"""Shared visual language for office-mode Qt surfaces."""

from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QWidget

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.script.workbench.theme import get_workbench_colors


OFFICE_BUBBLE_PAD_V = scale_px(7, min_abs=6)
OFFICE_BUBBLE_PAD_H = scale_px(10, min_abs=8)


def create_office_accent_bar(parent: QWidget) -> QWidget:
    bar = QWidget(parent)
    bar.setObjectName("OfficeAccentBar")
    bar.setFixedHeight(scale_px(5, min_abs=4))
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for object_name in ("OfficeAccentCyan", "OfficeAccentPink"):
        segment = QFrame(bar)
        segment.setObjectName(object_name)
        layout.addWidget(segment, 1)
    return bar


def office_stylesheet(mode: str | None = None) -> str:
    c = get_workbench_colors(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    control_height = scale_px(34, min_abs=30)
    compact_height = scale_px(31, min_abs=27)
    horizontal_padding = scale_px(11, min_abs=9)
    return f"""
        QWidget#OfficeWorkbenchPage, QDialog#OfficeApprovalDialog {{
            color: {c.text};
            font-family: '{font_family}';
        }}
        QWidget#OfficeWorkbenchPage * , QDialog#OfficeApprovalDialog * {{
            font-family: '{font_family}';
        }}
        QWidget#OfficeWorkbenchPage {{ background: transparent; }}
        QDialog#OfficeApprovalDialog {{
            background: {c.canvas};
            color: {c.text};
        }}
        QMessageBox#OfficeConfirmDialog {{
            background: {c.canvas};
            color: {c.text};
        }}
        QMessageBox#OfficeConfirmDialog QLabel {{ color: {c.text}; }}
        QMessageBox#OfficeConfirmDialog QPushButton {{
            min-height: {control_height}px;
            padding: 0px {horizontal_padding}px;
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            font-weight: 600;
        }}
        QMessageBox#OfficeConfirmDialog QPushButton:hover {{
            background: {c.surface_hover};
            border-color: {c.cyan};
        }}
        QMessageBox#OfficeConfirmDialog QPushButton:default {{
            background: {c.pink};
            color: {c.canvas};
            border-color: {c.pink};
        }}
        QMessageBox#OfficeConfirmDialog QPushButton#OfficeConfirmDelete {{
            background: {c.danger};
            color: {c.canvas};
            border-color: {c.danger};
        }}
        QMessageBox#OfficeConfirmDialog QPushButton#OfficeConfirmDelete:hover {{
            background: {c.pink_hover};
            color: {c.canvas};
            border-color: {c.pink_hover};
        }}
        QWidget#OfficeAccentBar {{
            background: {c.border_strong};
            border: {border}px solid {c.border_strong};
        }}
        QFrame#OfficeAccentCyan {{ background: {c.cyan}; border: none; }}
        QFrame#OfficeAccentPink {{ background: {c.pink}; border: none; }}

        QWidget#OfficeWorkbenchPage QFrame#SettingsPageHeader {{
            background: transparent;
            border: none;
        }}
        QWidget#OfficeWorkbenchPage QLabel#SettingsPageTitle {{
            color: {c.text};
            font-weight: 700;
        }}
        QWidget#OfficeWorkbenchPage QLabel#SettingsPageDescription {{
            color: {c.text_muted};
        }}
        QWidget#OfficeWorkbenchPage QFrame#SettingsSection {{
            background: {c.surface};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
        }}
        QWidget#OfficeWorkbenchPage QLabel#SettingsSectionTitle {{
            color: {c.text};
            font-weight: 700;
        }}
        QLabel#OfficeTaskTitle, QLabel#OfficeApprovalTitle {{
            color: {c.text};
            font-weight: 700;
        }}
        QLabel#OfficeFieldLabel, QLabel#OfficeSelectionHint,
        QLabel#OfficeApprovalReason, QLabel#OfficeApprovalScope,
        QLabel#OfficeApprovalCommandLabel {{ color: {c.text_muted}; }}
        QLabel#OfficeApprovalKicker {{
            color: {c.pink};
            font-weight: 700;
        }}
        QLabel#OfficeErrorLabel {{
            color: {c.danger};
            background: {c.surface};
            border: {border}px solid {c.border};
            border-left: {scale_px(3, min_abs=2)}px solid {c.danger};
            padding: {scale_px(7, min_abs=5)}px;
        }}
        QLabel#OfficeStatusBadge {{
            min-width: {scale_px(62, min_abs=56)}px;
            padding: {scale_px(5, min_abs=4)}px {scale_px(8, min_abs=6)}px;
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            color: {c.text_muted};
            background: {c.surface_raised};
            font-weight: 600;
        }}
        QLabel#OfficeStatusBadge[tone="active"],
        QLabel#OfficeStatusBadge[tone="success"] {{
            color: {c.canvas};
            background: {c.cyan};
            border-color: {c.cyan};
        }}
        QLabel#OfficeStatusBadge[tone="warning"] {{
            color: {c.canvas};
            background: {c.warning};
            border-color: {c.warning};
        }}
        QLabel#OfficeStatusBadge[tone="danger"] {{
            color: {c.canvas};
            background: {c.danger};
            border-color: {c.danger};
        }}

        QWidget#OfficeWorkbenchPage QPushButton,
        QDialog#OfficeApprovalDialog QPushButton {{
            min-height: {control_height}px;
            padding: 0px {horizontal_padding}px;
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            font-weight: 600;
        }}
        QWidget#OfficeWorkbenchPage QPushButton:hover,
        QDialog#OfficeApprovalDialog QPushButton:hover {{
            background: {c.surface_hover};
            border-color: {c.cyan};
        }}
        QWidget#OfficeWorkbenchPage QPushButton:pressed,
        QDialog#OfficeApprovalDialog QPushButton:pressed {{
            border-color: {c.pink};
        }}
        QWidget#OfficeWorkbenchPage QPushButton:disabled,
        QDialog#OfficeApprovalDialog QPushButton:disabled {{
            color: {c.text_dim};
            background: {c.surface};
            border-color: {c.border};
        }}
        QWidget#OfficeWorkbenchPage QPushButton#OfficeModeSegment {{
            min-height: {compact_height}px;
            padding: 0px {scale_px(9, min_abs=7)}px;
            background: {c.surface};
            color: {c.text_muted};
        }}
        QWidget#OfficeWorkbenchPage QPushButton#OfficeModeSegment[officeMode="companion"]:checked {{
            background: {c.cyan};
            color: {c.canvas};
            border-color: {c.cyan};
        }}
        QWidget#OfficeWorkbenchPage QPushButton#OfficeModeSegment[officeMode="office"]:checked {{
            background: {c.pink};
            color: {c.canvas};
            border-color: {c.pink};
        }}
        QWidget#OfficeWorkbenchPage QPushButton#OfficeSubmitButton {{
            min-width: {scale_px(112, min_abs=100)}px;
            background: {c.cyan};
            color: {c.canvas};
            border-color: {c.cyan};
        }}
        QWidget#OfficeWorkbenchPage QPushButton#OfficeSubmitButton:hover {{
            background: {c.pink_hover};
            color: {c.canvas};
            border-color: {c.pink_hover};
        }}
        QDialog#OfficeApprovalDialog QPushButton#OfficeApprovalAllowTask {{
            min-width: {scale_px(112, min_abs=100)}px;
            background: {c.pink};
            color: {c.canvas};
            border-color: {c.pink};
        }}
        QDialog#OfficeApprovalDialog QPushButton#OfficeApprovalAllowTask:hover {{
            background: {c.pink_hover};
            border-color: {c.pink_hover};
        }}
        QDialog#OfficeApprovalDialog QPushButton#OfficeApprovalAllow {{
            background: {c.cyan};
            color: {c.canvas};
            border-color: {c.cyan};
        }}
        QDialog#OfficeApprovalDialog QPushButton#OfficeApprovalAllow:hover {{
            background: {c.surface_hover};
            color: {c.text};
            border-color: {c.cyan};
        }}
        QDialog#OfficeApprovalDialog QPushButton#OfficeApprovalReject:hover {{
            background: {c.danger};
            color: {c.canvas};
            border-color: {c.danger};
        }}

        QWidget#OfficeWorkbenchPage QToolButton {{
            min-height: {compact_height}px;
            padding: 0px {scale_px(8, min_abs=6)}px;
            color: {c.text};
            background: {c.surface_raised};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
        }}
        QWidget#OfficeWorkbenchPage QToolButton#OfficeNewTaskButton:hover,
        QWidget#OfficeWorkbenchPage QToolButton#OfficeBrowseButton:hover {{
            background: {c.surface_hover};
            border-color: {c.cyan};
        }}
        QWidget#OfficeWorkbenchPage QToolButton#OfficeDeleteTaskButton:hover,
        QWidget#OfficeWorkbenchPage QToolButton#OfficeCancelButton:hover {{
            background: {c.surface_hover};
            color: {c.danger};
            border-color: {c.danger};
        }}
        QWidget#OfficeWorkbenchPage QToolButton:disabled {{
            color: {c.text_dim};
            background: {c.surface};
        }}

        QListWidget#OfficeTaskHistory, QListWidget#OfficeTodoList,
        QPlainTextEdit#OfficeReasoning,
        QPlainTextEdit#OfficeEvents, QPlainTextEdit#OfficePrompt,
        QPlainTextEdit#OfficeApprovalCommand, QLineEdit#OfficeWorkspace,
        QComboBox#OfficeReasoningEffort {{
            background: {c.surface};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            selection-background-color: {c.surface_hover};
            selection-color: {c.pink};
        }}
        QPlainTextEdit#OfficeReasoning,
        QPlainTextEdit#OfficeEvents, QPlainTextEdit#OfficePrompt,
        QPlainTextEdit#OfficeApprovalCommand, QLineEdit#OfficeWorkspace {{
            padding: {scale_px(7, min_abs=5)}px;
        }}
        QPlainTextEdit#OfficePrompt:focus, QLineEdit#OfficeWorkspace:focus,
        QComboBox#OfficeReasoningEffort:focus {{ border-color: {c.cyan}; }}
        QWidget#OfficeWorkbenchPage QLineEdit,
        QWidget#OfficeWorkbenchPage QComboBox {{ min-height: {control_height}px; }}
        QComboBox#OfficeReasoningEffort {{
            padding: 0px {scale_px(28, min_abs=24)}px 0px {scale_px(8, min_abs=6)}px;
        }}
        QComboBox#OfficeReasoningEffort::drop-down {{
            width: {scale_px(24, min_abs=20)}px;
            border: none;
        }}
        QComboBox#OfficeReasoningEffort QAbstractItemView {{
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border_strong};
            selection-background-color: {c.surface_hover};
            selection-color: {c.pink};
        }}
        QListWidget#OfficeTaskHistory {{
            outline: none;
        }}
        QListWidget#OfficeTaskHistory::item {{
            padding: {scale_px(9, min_abs=7)}px {scale_px(8, min_abs=6)}px;
            border-radius: {radius}px;
        }}
        QListWidget#OfficeTaskHistory::item:hover {{ background: {c.surface_hover}; }}
        QListWidget#OfficeTaskHistory::item:selected {{
            background: {c.surface_hover};
            color: {c.pink};
            border-left: {scale_px(3, min_abs=2)}px solid {c.pink};
        }}
        QListWidget#OfficeTodoList::item {{
            padding: {scale_px(7, min_abs=5)}px;
            border-bottom: {border}px solid {c.border};
        }}
        QTabWidget#OfficeTaskTabs::pane {{
            border: {border}px solid {c.border};
            background: {c.canvas};
        }}
        QTabBar::tab {{
            background: {c.surface};
            color: {c.text_muted};
            border: {border}px solid {c.border};
            border-bottom: none;
            padding: {scale_px(7, min_abs=5)}px {scale_px(14, min_abs=11)}px;
        }}
        QTabBar::tab:hover {{ color: {c.text}; border-top-color: {c.cyan}; }}
        QTabBar::tab:selected {{
            background: {c.canvas};
            color: {c.pink};
            border-top-color: {c.pink};
        }}
        QSplitter#OfficeMainSplitter::handle {{
            background: {c.canvas};
            width: {scale_px(6, min_abs=4)}px;
        }}

        QScrollArea#OfficeConversation {{
            background: transparent;
            border: none;
        }}
        QScrollArea#OfficeConversation > QWidget#qt_scrollarea_viewport {{
            background: transparent;
        }}
        QWidget#OfficeConversationContainer {{ background: transparent; }}
        QLabel#OfficeChatSystem {{
            color: {c.text_muted};
            background: transparent;
            padding: {scale_px(3, min_abs=2)}px {scale_px(8, min_abs=6)}px;
            font-size: {scale_px(11, min_abs=10)}px;
        }}
        QLabel#OfficeChatSender {{
            color: {c.text_muted};
            background: transparent;
            font-size: {scale_px(10, min_abs=9)}px;
        }}
        QFrame#OfficeChatBubble {{
            border-radius: {scale_px(10, min_abs=8)}px;
            padding: {OFFICE_BUBBLE_PAD_V}px {OFFICE_BUBBLE_PAD_H}px;
            font-size: {scale_px(12, min_abs=11)}px;
        }}
        QFrame#OfficeChatBubble[side="assistant"] {{
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-top-left-radius: {scale_px(3, min_abs=2)}px;
        }}
        QFrame#OfficeChatBubble[side="user"] {{
            background: {c.cyan};
            color: {c.canvas};
            border: none;
            border-top-right-radius: {scale_px(3, min_abs=2)}px;
        }}
        QFrame#OfficeChatBubble[side="assistant"] QLabel#OfficeChatBubbleText {{
            color: {c.text};
            background: transparent;
        }}
        QFrame#OfficeChatBubble[side="user"] QLabel#OfficeChatBubbleText {{
            color: {c.canvas};
            background: transparent;
        }}

        QFrame#OfficeApprovalHeader {{
            background: {c.surface};
            border: {border}px solid {c.border};
            border-left: {scale_px(3, min_abs=2)}px solid {c.pink};
            border-radius: {radius}px;
        }}
        QLabel#OfficeApprovalIcon {{
            min-width: {scale_px(28, min_abs=24)}px;
            background: transparent;
        }}
        QLabel#OfficeApprovalScope {{
            background: {c.navigation};
            border: {border}px solid {c.border};
            border-left: {scale_px(3, min_abs=2)}px solid {c.cyan};
            padding: {scale_px(7, min_abs=5)}px;
        }}
        QPlainTextEdit#OfficeApprovalCommand {{
            font-family: 'Consolas', '{font_family}';
        }}

        QWidget#OfficeWorkbenchPage QMenu {{
            background: {c.surface_raised};
            color: {c.text};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            padding: {scale_px(3, min_abs=2)}px 0px;
        }}
        QWidget#OfficeWorkbenchPage QMenu::item {{
            padding: {scale_px(5, min_abs=4)}px {scale_px(18, min_abs=12)}px;
        }}
        QWidget#OfficeWorkbenchPage QMenu::item:selected {{
            background: {c.surface_hover};
        }}
        QWidget#OfficeWorkbenchPage QMenu::item:disabled {{
            color: {c.text_dim};
        }}

        QScrollBar:vertical {{
            background: {c.canvas};
            width: {scale_px(10, min_abs=8)}px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {c.border_strong};
            min-height: {scale_px(26, min_abs=22)}px;
            border-radius: {scale_px(3, min_abs=2)}px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c.pink}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
            border: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """


__all__ = ["create_office_accent_bar", "office_stylesheet", "OFFICE_BUBBLE_PAD_H"]
