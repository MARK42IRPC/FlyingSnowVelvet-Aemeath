"""音响右键菜单族共享样式。"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QPainter, QColor

from config.config import SPEAKER_SEARCH_UI
from lib.core.qt_bridge.colors import COLORS, UI_THEME
from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px
from lib.core.unified_draw import Layer, get_layer_manager

_C_BORDER = UI_THEME['border']
_C_MID = UI_THEME['mid']
_C_BG = UI_THEME['bg']
_C_TEXT = UI_THEME['text']
_C_ICON = UI_THEME['icon']
_C_HL = UI_THEME['highlight']
_C_ENTRY_BG = QColor(*SPEAKER_SEARCH_UI.get('entry_bg_color', (255, 255, 255)))
_C_ACTION_BG = COLORS['pink']
_C_ACTION_BORDER = COLORS['black']
_C_ACTION_MID = COLORS['cyan']
_C_ACTION_TEXT = COLORS['black']
_C_ACTION_HOVER = UI_THEME['deep_pink']

_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


def paint_speaker_menu_panel(painter: QPainter, rect: QRect) -> QRect:
    """绘制音响菜单族共享三层面板，并返回内容区。"""
    painter.fillRect(rect, _C_BORDER)
    painter.fillRect(rect.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), _C_MID)
    content_rect = rect.adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER)
    painter.fillRect(content_rect, _C_BG)
    return content_rect


def paint_speaker_action_button(
    painter: QPainter,
    rect: QRect,
    *,
    hovered: bool = False,
    pressed: bool = False,
) -> QRect:
    """绘制音响菜单族统一动作按钮，并返回内容区。"""
    painter.fillRect(rect, _C_ACTION_BORDER)
    mid_rect = rect.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
    painter.fillRect(mid_rect, _C_ACTION_MID)
    content_rect = mid_rect.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
    if hovered:
        painter.fillRect(content_rect, _C_ACTION_HOVER)
        content_rect = content_rect.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
    painter.fillRect(content_rect, _C_HL if pressed else _C_ACTION_BG)
    return content_rect


class SpeakerActionButtonMixin:
    """音响菜单族动作按钮通用交互外观。"""

    def _init_speaker_action_button(self, width: int, height: int) -> None:
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)
        self._hovered = False
        self._pressed = False
        self._label_font = get_ui_font()
        self._label_font.setBold(True)

    def _paint_action_button_shell(self, painter: QPainter) -> QRect:
        painter.setRenderHint(QPainter.Antialiasing, False)
        return paint_speaker_action_button(
            painter,
            self.rect(),
            hovered=getattr(self, '_hovered', False),
            pressed=getattr(self, '_pressed', False),
        )

    def _begin_action_press(self) -> None:
        self._pressed = True
        try:
            self.grabMouse()
        except RuntimeError:
            pass
        self.update()

    def _finish_action_press(self, *, commit: bool) -> bool:
        was_pressed = getattr(self, '_pressed', False)
        self._pressed = False
        try:
            if self.mouseGrabber() is self:
                self.releaseMouse()
        except RuntimeError:
            pass
        self.update()
        return was_pressed and commit

    def _cancel_action_press(self) -> None:
        self._finish_action_press(commit=False)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)


__all__ = [
    "_C_BORDER",
    "_C_MID",
    "_C_BG",
    "_C_TEXT",
    "_C_ICON",
    "_C_HL",
    "_C_ENTRY_BG",
    "_C_ACTION_TEXT",
    "_LAYER",
    "_BORDER",
    "SpeakerActionButtonMixin",
    "paint_speaker_action_button",
    "paint_speaker_menu_panel",
]
