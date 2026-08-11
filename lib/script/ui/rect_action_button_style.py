"""右键矩形功能按钮的共享绘制样式。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter

from config.config import UI
from lib.core.qt_bridge.font import get_ui_font
from lib.core.anchor_utils import animate_opacity, apply_ui_opacity
from lib.core.graphics.application_visuals import build_rect_action_button_visual
from lib.core.graphics.types import FontSpec
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.unified_draw import Layer, get_layer_manager


_DRAW_BACKEND = QtDrawBackend()


def paint_rect_action_button(painter: QPainter, rect, font, text: str, hovered: bool = False) -> None:
    """Execute the shared pet action-button visual through Qt."""
    visual = build_rect_action_button_visual(
        rect.width(),
        rect.height(),
        text,
        FontSpec(font.family(), font.pixelSize(), font.bold()),
        hovered=hovered,
    )
    _DRAW_BACKEND.render(visual.batch, painter)


class RectActionButton(QWidget):
    """“鼠标穿透同款”矩形按钮共享基类。"""

    def __init__(self, width: int, height: int, description: str = ""):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._font = get_ui_font()
        self._font.setBold(True)

        self._visible = False
        self._hovered = False
        self._description = description

    def _button_text(self) -> str:
        return ""

    def _animate(self, target: float) -> None:
        animate_opacity(self._anim, self._opacity, target)

    def set_direct_opacity(self, target: float) -> None:
        self._opacity.setOpacity(apply_ui_opacity(target))

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_rect_action_button(
            painter,
            self.rect(),
            self._font,
            self._button_text(),
            hovered=self._hovered,
        )

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)
