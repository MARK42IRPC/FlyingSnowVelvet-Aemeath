"""工作台共享控件（档位 D 宿主）。

`WorkbenchPetAboutButton` 与 `WorkbenchOverviewPage` 是工作台外壳共用的小控件。

落进档位 D 后解耦掉的产品依赖（宿主不得 import `lib.script`）：

- 主题色：后端中立 `visuals/workbench_theme.get_workbench_colors`。
- 字体：`tier_d_seams.ui_font`。
- 动作按钮底壳（原产品面 `speaker_menu_style.paint_speaker_action_button`）：这里按同一份
  后端中立配方（`visuals/controls.SpeakerActionButtonSpec`）就地绘制，绘制宿主经
  `tier_d_seams.create_painter_host()`。
- 窗口按钮：档位 D 同族 `window_buttons.create_window_button` 再导出。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.window_buttons import (
    create_window_button as create_window_button,
)
from lib.core.render.visuals import controls as control_visuals
from lib.core.render.visuals.types import Point
from lib.core.render.visuals.workbench_theme import get_workbench_colors

get_ui_font = tier_d_seams.ui_font


def paint_speaker_action_button(painter, rect, *, hovered: bool = False, pressed: bool = False):
    """绘制音响菜单族统一动作按钮，并返回内容区（``QRect``）。

    与产品面 `speaker_menu_style.paint_speaker_action_button` 同源：同一份后端中立配方
    （`SpeakerActionButtonSpec`）、同一个绘制宿主。``rect`` 的原点原样保留。
    """
    host = tier_d_seams.create_painter_host()
    core_rect = host.rect(rect)
    visual = control_visuals.SpeakerActionButtonSpec(
        core_rect.width,
        core_rect.height,
        hovered=hovered,
        pressed=pressed,
        origin=Point(core_rect.x, core_rect.y),
    ).build_visual()
    host.render(visual.batch, painter)
    return host.qrect(visual.content_rect)


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
        painter.setPen(tier_d_seams.painter_color(get_workbench_colors().text))
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


__all__ = ["WorkbenchOverviewPage", "WorkbenchPetAboutButton", "paint_speaker_action_button"]
