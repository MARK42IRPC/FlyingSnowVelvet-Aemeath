"""Shared widgets used by the unified workbench shell."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px
from lib.script.ui.speaker_menu_style import paint_speaker_action_button
from lib.script.workbench.theme import get_workbench_colors


def create_window_button(
    parent: QWidget,
    standard_icon: QStyle.StandardPixmap,
    tooltip: str,
    callback: Callable[[], None],
    *,
    danger: bool = False,
) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("WorkbenchWindowButton")
    button.setProperty("danger", danger)
    button.setAutoRaise(True)
    button.setIcon(parent.style().standardIcon(standard_icon))
    button.setIconSize(QSize(scale_px(15, min_abs=13), scale_px(15, min_abs=13)))
    button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


class WorkbenchPetAboutButton(QToolButton):
    """About menu trigger painted with the desktop pet action-button style."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkbenchAboutButton")
        self.setText("关于")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(scale_px(70, min_abs=64), scale_px(34, min_abs=30))
        self._hovered = False
        self._label_font = get_ui_font(size=scale_px(11, min_abs=10))
        self._label_font.setBold(True)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        content_rect = paint_speaker_action_button(
            painter,
            self.rect(),
            hovered=self._hovered or bool(self.property("active")),
            pressed=self.isDown(),
        )
        painter.setPen(QColor(get_workbench_colors().text))
        painter.setFont(self._label_font)
        painter.drawText(content_rect, Qt.AlignCenter, self.text())

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class WorkbenchOverviewPage(QWidget):
    def __init__(
        self,
        navigate: Callable[[str], None],
        sections: Iterable[tuple[str, Iterable[tuple[str, str]]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WorkbenchOverviewPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(
            scale_px(28, min_abs=22),
            scale_px(24, min_abs=20),
            scale_px(28, min_abs=22),
            scale_px(24, min_abs=20),
        )
        root.setSpacing(scale_px(16, min_abs=12))

        for section_title, actions in sections:
            section = QFrame(self)
            section.setObjectName("WorkbenchOverviewSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(
                scale_px(16, min_abs=13),
                scale_px(14, min_abs=12),
                scale_px(16, min_abs=13),
                scale_px(16, min_abs=13),
            )
            section_layout.setSpacing(scale_px(9, min_abs=7))

            section_label = QLabel(section_title, section)
            section_label.setObjectName("WorkbenchOverviewSectionTitle")
            section_font = get_ui_font(size=scale_px(14, min_abs=12))
            section_font.setBold(True)
            section_label.setFont(section_font)
            section_layout.addWidget(section_label)

            action_row = QHBoxLayout()
            action_row.setContentsMargins(0, 0, 0, 0)
            action_row.setSpacing(scale_px(8, min_abs=6))
            for page_id, label in actions:
                button = QPushButton(label, section)
                button.setObjectName("WorkbenchQuickAction")
                button.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
                button.clicked.connect(lambda _checked=False, target=page_id: navigate(target))
                action_row.addWidget(button, 1)
            action_row.addStretch(0)
            section_layout.addLayout(action_row)
            root.addWidget(section)

        root.addStretch(1)
