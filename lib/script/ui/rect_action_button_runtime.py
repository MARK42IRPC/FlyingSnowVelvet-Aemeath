"""右键矩形动作按钮族的共享运行时（Qt-free 的“描述 + 宿主”装配）。

右键矩形动作按钮一族（鼠标穿透 / 放大缩小 / 关闭 / 启动鸣潮 / 模式切换 /
更多功能，原 `rect_action_button_style.py`）形状与状态完全一致，差异只有“文字、出现在族的哪个节点、点下去做什么”。
共享描述是 ``lib/core/render/visuals/controls.py`` 的 ``RectActionButtonControl``，
共享绘制是 ``application_visuals.build_rect_action_button_visual``；本模块只做装配：
建描述、建宿主、把悬停/指针翻译成控件自己的产品意图。

控件本体不再继承 ``QWidget``、不再 ``import PyQt5``：它们持有本模块给的宿主，
``width`` / ``height`` / ``x`` / ``y`` / ``isVisible`` 全部转发宿主。落位仍按档位 2/3：
宿主由 ``RightClickUiLayer`` 用 ``adopt()`` 收编进右键 UI 一层，每帧只移动一个原生窗口。
"""

from __future__ import annotations

from collections.abc import Callable

from config.config import UI
from lib.core.render.visuals import controls
from lib.core.render.visuals.types import FontSpec
from lib.core.render.layers import Layer
from lib.script.ui import render_bridge


_UI_OPACITY_SCALE = controls.ui_opacity_scale
_FADE_MS = UI['ui_fade_duration']


def _font_spec(font) -> FontSpec:
    """把后端 UI 字体对象压成后端无关的 ``FontSpec``（与旧 paint 逐字一致）。"""
    return FontSpec(font.family(), font.pixelSize(), font.bold())


class RectActionButtonRuntime:
    """一个矩形动作按钮的描述 + 宿主装配。"""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        text: str,
        description: str,
        on_pointer: Callable,
        on_clickthrough: Callable[[bool], None] | None = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.description = str(description or "")
        self._on_pointer = on_pointer
        self._on_clickthrough = on_clickthrough

        self._font = render_bridge.ui_font()
        self._font.setBold(True)

        self.control = controls.RectActionButtonControl(
            width=self.width,
            height=self.height,
            text=text,
            fade_duration_ms=_FADE_MS,
            paint_layer=int(Layer.PET_UI),
            opacity_scale=_UI_OPACITY_SCALE,
        )

        self.host = render_bridge.create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer_event,
            on_hover_changed=self._on_hover,
            on_fade_out_finished=self._on_fade_out_finished,
            layer=Layer.PET_UI,
            fade_duration_ms=_FADE_MS,
            fade_out_duration_ms=_FADE_MS,
        )
        self.host._description = self.description
        self.host.apply_size(self.width, self.height)

        self._event_center = None

    # ── 绘制 / 指针 ────────────────────────────────────────────────
    def _paint_batch(self):
        return self.control.build_visual(_font_spec(self._font)).batch

    def _on_pointer_event(self, event):
        return self._on_pointer(event)

    def _on_hover(self, hovered: bool) -> None:
        if self.control.hovered != bool(hovered):
            self.control.hovered = bool(hovered)
            self.host.update()

    def _on_fade_out_finished(self) -> None:
        if not self.control.visible:
            self.host.hide()

    # ── 尺寸 / 位置 / 可见性视图 ───────────────────────────────────
    def width_(self) -> int:
        return int(self.host.width())

    def height_(self) -> int:
        return int(self.host.height())

    def x(self) -> int:
        return int(self.host.x())

    def y(self) -> int:
        return int(self.host.y())

    def is_visible(self) -> bool:
        return bool(self.host.isVisible())

    def update(self) -> None:
        self.host.update()

    def hide(self) -> bool:
        """隐藏并返回“之前是否可见”，供淡出分支判断是否需要发粒子。"""
        was_visible = bool(self.control.visible)
        self.host.stop_animation()
        self.control.visible = False
        self.host.hide()
        return was_visible

    def move(self, x: int, y: int) -> None:
        self.host.move_to(int(x), int(y))

    def geometry_rect(self):
        """屏幕坐标系里的 ``Rect``（用于淡出粒子的区域）。"""
        return self.host.geometry_rect()

    # ── 淡入 / 淡出 ────────────────────────────────────────────────
    def fade_in(self) -> bool:
        if self.control.visible:
            return False
        self.control.visible = True
        self.host.show()
        self.host.fade_to(self.control.scaled_opacity(1.0), duration_ms=_FADE_MS)
        return True

    def fade_out(self) -> bool:
        if not self.control.visible:
            return False
        self.control.visible = False
        self.host.fade_to(
            self.control.scaled_opacity(0.0),
            duration_ms=_FADE_MS,
            fade_out=True,
        )
        return True

    def fade_out_with_particle(self, event_center) -> bool:
        """淡出并在旧几何处发 ``right_fade`` 消散粒子（与迁移前逐字一致）。"""
        if not self.control.visible:
            return False
        rect = self.geometry_rect()
        self.fade_out()
        from lib.core.event.center import Event, EventType
        event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (
                int(rect.x), int(rect.y),
                int(rect.x) + int(rect.width), int(rect.y) + int(rect.height),
            ),
        }))
        return True

    def set_direct_opacity(self, value: float) -> None:
        self.host.set_opacity(self.control.scaled_opacity(value))

    # ── 穿透切换 / 生命周期 ───────────────────────────────────────
    def set_clickthrough(self, enabled: bool) -> None:
        self.host.set_clickthrough(bool(enabled))
        if self._on_clickthrough is not None:
            self._on_clickthrough(bool(enabled))

    def cleanup(self) -> None:
        self.host.cleanup()


def emit_click_particle(control, event) -> None:
    """按左/右键在点击屏幕坐标发射 click / pink_click 粒子。"""
    particle_id = control.click_particle_id(event)
    if not particle_id:
        return
    from lib.script.ui._particle_helper import publish_click_particle_at

    publish_click_particle_at(particle_id, int(event.screen.x), int(event.screen.y))


__all__ = ["RectActionButtonRuntime", "emit_click_particle"]
