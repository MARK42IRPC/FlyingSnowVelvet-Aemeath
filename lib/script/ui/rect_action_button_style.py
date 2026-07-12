"""右键矩形功能按钮的共享绘制样式。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter

from config.config import COLORS, UI, UI_THEME
from config.font_config import get_ui_font
from config.scale import scale_px
from lib.core.anchor_utils import animate_opacity, apply_ui_opacity
from lib.core.unified_draw import Layer, get_layer_manager


def paint_rect_action_button(painter: QPainter, rect, font, text: str, hovered: bool = False) -> None:
    """绘制右键 UI 中统一样式的矩形功能按钮。"""
    painter.setRenderHint(QPainter.Antialiasing, False)
    layer = scale_px(2, min_abs=1)

    painter.fillRect(rect, COLORS['black'])

    cyan_rect = rect.adjusted(layer, layer, -layer, -layer)
    painter.fillRect(cyan_rect, COLORS['cyan'])

    if hovered:
        pink_border_rect = cyan_rect.adjusted(layer, layer, -layer, -layer)
        painter.fillRect(pink_border_rect, UI_THEME['deep_pink'])
        content_rect = pink_border_rect.adjusted(layer, layer, -layer, -layer)
    else:
        content_rect = rect.adjusted(layer * 2, layer * 2, -layer * 2, -layer * 2)

    painter.fillRect(content_rect, COLORS['pink'])
    painter.setPen(COLORS['black'])
    painter.setFont(font)
    painter.drawText(content_rect, 0x84, text)  # Qt.AlignCenter


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
