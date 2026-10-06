"""音响右键菜单族共享样式（后端中立门面）。

本模块过去是一个 Qt 混入：既 import ``QPainter``/``QColor``，又自己
``create_draw_backend()`` 建了一份执行器。两件事现在都收了：

- 颜色：``_C_*`` 常量改成核心 ``Color``（共享色板是唯一事实源）；QSS 要的
  ``#rrggbb`` 文本由 ``render_bridge.qt_color_name()`` 给出。
- 绘制：面板壳与动作按钮的配方已下沉到
  ``visuals/application_visuals.build_speaker_panel_visual()`` /
  ``build_speaker_action_button_visual()``（状态名与描述层的
  ``SpeakerActionButtonControl`` 同源），真实像素由
  ``render_bridge.create_painter_host()`` 在调用方自己的 ``QPainter`` 上执行。

因此模块本身不再 import ``PyQt5``：``paint_*`` 只负责把 paintEvent 的 ``QRect``
换成核心几何、要一份批次、交给注入的宿主画，再把内容区换回 ``QRect``。
``SpeakerActionButtonMixin`` 去掉 ``setWindowFlags``/``setFixedSize``/``register``
这类窗口事实——混入的对象本身就是 ``QWidget`` 子类，窗口标志与图层注册由它自己设置。
"""

from __future__ import annotations

from config.config import SPEAKER_SEARCH_UI
from config.scale import scale_px
from lib.core.render.visuals import controls as control_visuals
from lib.core.render.visuals.palette import COLORS, UI_THEME
from lib.core.render.visuals.types import Color, Point
from lib.script.ui import render_bridge

_C_BORDER = UI_THEME['border']
_C_MID = UI_THEME['mid']
_C_BG = UI_THEME['bg']
_C_TEXT = UI_THEME['text']
_C_ICON = UI_THEME['icon']
_C_HL = UI_THEME['highlight']
_C_ENTRY_BG = Color(*SPEAKER_SEARCH_UI.get('entry_bg_color', (255, 255, 255)))
_C_ACTION_BG = COLORS['pink']
_C_ACTION_BORDER = COLORS['black']
_C_ACTION_MID = COLORS['cyan']
_C_ACTION_TEXT = COLORS['black']
_C_ACTION_HOVER = UI_THEME['deep_pink']

_LAYER = scale_px(2, min_abs=1)
_BORDER = _LAYER * 2


def qt_color_name(token: str) -> str:
    """取共享色板里的颜色，返回 QSS 可用的 ``#rrggbb`` 文本。"""
    return render_bridge.qt_color_name(token)


def paint_speaker_menu_panel(painter, rect):
    """绘制音响菜单族共享三层面板，并返回内容区（``QRect``）。

    ``rect`` 的原点会原样保留：同一个 painter 上可能并排画多个部件
    （搜索框把输入区和按钮画在一起），丢掉原点会把面板挪到别处。
    """
    host = render_bridge.create_painter_host()
    core_rect = host.rect(rect)
    visual = control_visuals.SpeakerPanelSpec(
        core_rect.width,
        core_rect.height,
        origin=Point(core_rect.x, core_rect.y),
    ).build_visual()
    host.render(visual.batch, painter)
    return host.qrect(visual.content_rect)


def paint_speaker_action_button(
    painter,
    rect,
    *,
    hovered: bool = False,
    pressed: bool = False,
):
    """绘制音响菜单族统一动作按钮，并返回内容区（``QRect``）。

    与 ``paint_speaker_menu_panel`` 一样，``rect`` 的原点是调用方的坐标事实，
    不能丢：搜索框的按钮区在 ``x = _INPUT_W`` 处，丢了原点就会被输入区盖住。
    """
    host = render_bridge.create_painter_host()
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


class SpeakerActionButtonMixin:
    """音响菜单族动作按钮通用交互外观（不含窗口事实）。"""

    def _init_speaker_action_button_state(self) -> None:
        """初始化交互状态与标签字体。

        窗口标志、固定尺寸、指针光标与图层注册是宿主 QWidget 自己的事实，
        由使用这个混入的控件设置；本方法只管悬停 / 按下状态与标签字体。
        """
        self._hovered = False
        self._pressed = False
        self._label_font = render_bridge.ui_font()
        self._label_font.setBold(True)

    def _paint_action_button_shell(self, painter):
        host = render_bridge.create_painter_host()
        rect = host.rect(self.rect())
        visual = control_visuals.SpeakerActionButtonSpec(
            rect.width,
            rect.height,
            hovered=getattr(self, '_hovered', False),
            pressed=getattr(self, '_pressed', False),
        ).build_visual()
        host.render(visual.batch, painter)
        return host.qrect(visual.content_rect)

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
    "_C_ACTION_BG",
    "_C_ACTION_BORDER",
    "_C_ACTION_MID",
    "_C_ACTION_TEXT",
    "_C_ACTION_HOVER",
    "_LAYER",
    "_BORDER",
    "SpeakerActionButtonMixin",
    "paint_speaker_action_button",
    "paint_speaker_menu_panel",
    "qt_color_name",
]
