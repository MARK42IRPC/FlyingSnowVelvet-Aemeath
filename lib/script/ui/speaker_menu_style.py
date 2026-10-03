"""音响右键菜单族共享样式。"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QPainter, QColor

from config.config import SPEAKER_SEARCH_UI
from lib.core.render.visuals.commands import DrawBatch
from lib.core.render.visuals.panel_visuals import (
    action_button_commands,
    panel_shell_commands,
)
from lib.core.render.visuals.types import Rect
from lib.script.ui.render_bridge import create_draw_backend, qt_color, ui_font as get_ui_font
from config.scale import scale_px
from lib.core.render.layers import get_layer_manager, WindowLayer

_C_BORDER = qt_color('border')
_C_MID = qt_color('mid')
_C_BG = qt_color('bg')
_C_TEXT = qt_color('text')
_C_ICON = qt_color('icon')
_C_HL = qt_color('highlight')
_C_ENTRY_BG = QColor(*SPEAKER_SEARCH_UI.get('entry_bg_color', (255, 255, 255)))
_C_ACTION_BG = qt_color('pink')
_C_ACTION_BORDER = qt_color('black')
_C_ACTION_MID = qt_color('cyan')
_C_ACTION_TEXT = qt_color('black')
_C_ACTION_HOVER = qt_color('deep_pink')

_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2
_DRAW_BACKEND = create_draw_backend()


def _to_core_rect(rect: QRect) -> Rect:
    return Rect(rect.x(), rect.y(), rect.width(), rect.height())


def _to_qt_rect(rect: Rect) -> QRect:
    return QRect(int(rect.x), int(rect.y), int(rect.width), int(rect.height))


def paint_speaker_menu_panel(painter: QPainter, rect: QRect) -> QRect:
    """绘制音响菜单族共享三层面板，并返回内容区。"""
    commands, content = panel_shell_commands(_to_core_rect(rect), inset=_LAYER)
    _DRAW_BACKEND.render(DrawBatch(tuple(commands)), painter)
    return _to_qt_rect(content)


def paint_speaker_action_button(
    painter: QPainter,
    rect: QRect,
    *,
    hovered: bool = False,
    pressed: bool = False,
) -> QRect:
    """绘制音响菜单族统一动作按钮，并返回内容区。"""
    if hovered:
        state = "pressed" if pressed else "hover"
    else:
        state = "pressed_flat" if pressed else "normal"
    commands, content = action_button_commands(
        _to_core_rect(rect),
        None,
        state=state,
        inset=_LAYER,
    )
    _DRAW_BACKEND.render(DrawBatch(tuple(commands)), painter)
    return _to_qt_rect(content)


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
        get_layer_manager().register(self, WindowLayer.PET_UI)
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
