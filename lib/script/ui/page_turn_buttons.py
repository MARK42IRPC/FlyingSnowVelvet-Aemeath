"""翻页按钮 - 上一页 / 下一页

宽度 120px，高度 20px（与各面板行高一致）。
样式与音响控制按钮一致：2px 黑外框 + 2px 青色中框 + 粉色背景 + 黑色箭头图标。

定位规则：
  - 上一页按钮：左上锚点对齐宿主面板的左下锚点
  - 下一页按钮：右上锚点对齐宿主面板的右下锚点

本模块不再继承 ``QWidget``：箭头方向、悬停/按下状态与绘制批次都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``PageTurnButtonControl``，绘制事实源是
``application_visuals.build_page_turn_button_visual``），真实窗口、透明度动画与指针
翻译由后端窗口宿主持有。落位仍走 ``visuals/layout.py`` 的共享解算。
"""

from __future__ import annotations

from typing import Callable

from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.layers import Layer
from lib.core.render.visuals import controls
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.ui import render_bridge

# ── 尺寸 ──────────────────────────────────────────────────────────────
BTN_W = scale_px(120, min_abs=1)  # 宽度（px）
BTN_H = scale_px(20, min_abs=1)   # 高度（px），与面板行高一致


class _PageTurnButton:
    """翻页按钮（描述 + 宿主装配）。

    对外接口保持迁移前的形状（``show_btn`` / ``hide_btn`` / ``move`` / ``isVisible`` /
    ``width`` / ``height`` / ``x`` / ``y`` / ``close``），族内跟随者继续用
    ``widget_global_rect()`` 与 ``isVisible()`` 测量它。
    """

    def __init__(self, direction: int, callback: Callable[[], None] | None) -> None:
        """
        direction: -1 = 上一页，+1 = 下一页
        callback:  点击时调用的无参函数
        """
        self._direction = direction
        self._callback = callback

        self._control = controls.PageTurnButtonControl(
            direction=direction,
            width=BTN_W,
            height=BTN_H,
            fade_duration_ms=150,
            paint_layer=int(Layer.PET_UI),
            opacity_scale=controls.ui_opacity_scale,
        )

        self._host = render_bridge.create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_pointer_move=self._on_pointer_move,
            on_pointer_release=self._on_pointer_release,
            on_hover_changed=self._on_hover_changed,
            on_fade_out_finished=self._on_fade_out_finished,
            layer=Layer.PET_UI,
            fade_duration_ms=self._control.fade_duration_ms,
            fade_out_duration_ms=self._control.fade_duration_ms,
            # 不抢夺键盘焦点（避免点击时导致输入框失焦）
            accepts_focus=False,
            pointing_cursor=True,
        )
        self._host.apply_size(BTN_W, BTN_H)
        #: 指针是否还在按钮矩形内。Qt 只在按下时投递 mouseMoveEvent，因此这就是
        #: 迁移前 ``self.rect().contains(event.pos())`` 的等价判定：松手时若指针已经
        #: 拖出按钮，就按"取消"处理、不触发翻页。
        self._pointer_inside = False

        self._event_center = get_event_center()
        self._event_center.subscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle
        )

    # ── 显示/隐藏 ─────────────────────────────────────────────────────

    def show_btn(self) -> None:
        if self._control.visible:
            return
        self._control.visible = True
        self._host.show()
        self._animate(1.0)

    def hide_btn(self) -> None:
        self._cancel_action_press()
        if not self._control.visible:
            return
        self._control.visible = False
        self._animate(0.0, fade_out=True)

    def _animate(self, target: float, *, fade_out: bool = False) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=self._control.fade_duration_ms,
            fade_out=fade_out,
        )

    def _on_fade_out_finished(self) -> None:
        if not self._control.visible:
            self._host.hide()

    # ── 图形状态与指针 ────────────────────────────────────────────────

    def _paint_batch(self):
        return self._control.build_visual().batch

    def _on_hover_changed(self, hovered: bool) -> None:
        if self._control.hovered != bool(hovered):
            self._control.hovered = bool(hovered)
            self._host.update()

    def _begin_action_press(self) -> None:
        if self._control.pressed:
            return
        self._control.pressed = True
        self._host.update()

    def _finish_action_press(self, *, commit: bool) -> bool:
        was_pressed = self._control.pressed
        self._control.pressed = False
        self._host.update()
        return was_pressed and commit

    def _cancel_action_press(self) -> None:
        self._finish_action_press(commit=False)

    def _on_pointer(self, event):
        particle_id = self._control.click_particle_id(event)
        if particle_id:
            publish_click_particle_at(
                particle_id, int(event.screen.x), int(event.screen.y)
            )
        if event.button == controls.BUTTON_LEFT:
            self._pointer_inside = True
            self._begin_action_press()
        return controls.PointerClick()

    def _on_pointer_move(self, event) -> None:
        self._pointer_inside = (
            0.0 <= event.local.x < self._control.width
            and 0.0 <= event.local.y < self._control.height
        )
        if self._pointer_inside != self._control.hovered:
            self._control.hovered = self._pointer_inside
            self._host.update()

    def _on_pointer_release(self) -> None:
        if not self._control.pressed:
            return
        if self._finish_action_press(commit=self._pointer_inside) and self._callback:
            self._callback()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    # ── 尺寸 / 位置 / 可见性视图（宿主转发）───────────────────────────

    def width(self) -> int:
        return int(self._host.width())

    def height(self) -> int:
        return int(self._host.height())

    def x(self) -> int:
        return int(self._host.x())

    def y(self) -> int:
        return int(self._host.y())

    def isVisible(self) -> bool:
        return bool(self._host.isVisible())

    def move(self, x: int, y: int) -> None:
        self._host.move_to(int(x), int(y))

    def update(self) -> None:
        self._host.update()

    def hide(self) -> None:
        self._cancel_action_press()
        self._host.hide()

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(
                EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle
            )
        except Exception:
            pass
        self._host.cleanup()

    def close(self) -> None:
        self.cleanup()


def make_page_buttons(prev_cb, next_cb) -> tuple['_PageTurnButton', '_PageTurnButton']:
    """
    创建一对翻页按钮。
    返回 (prev_btn, next_btn)。
    """
    return _PageTurnButton(-1, prev_cb), _PageTurnButton(1, next_cb)


def update_page_buttons_position(
    panel,
    prev_btn: '_PageTurnButton',
    next_btn: '_PageTurnButton',
    has_pages: bool,
) -> None:
    """
    根据宿主面板位置更新翻页按钮位置，并按需显示/隐藏。

    - prev_btn 左上锚点 = 面板左下锚点
    - next_btn 右上锚点 = 面板右下锚点（即 next_btn.x = panel.right - BTN_W）
    """
    # 面板可能是"右键 UI 一层"里的子控件，统一按屏幕坐标计算；
    # 两个按钮的落位解算都在 visuals/layout.py（档位 1）。
    panel_rect = render_bridge.widget_global_rect(panel)
    screen = render_bridge.screen_rect_for_point(
        point=panel_rect.center, fallback_widget=panel
    )

    prev_placement = render_bridge.resolve_placement(
        (BTN_W, BTN_H), panel_rect, screen,
        target_anchor_id='bottom_left', self_anchor_id='top_left',
    )
    prev_btn.move(prev_placement.x, prev_placement.y)

    next_placement = render_bridge.resolve_placement(
        (BTN_W, BTN_H), panel_rect, screen,
        target_anchor_id='bottom_right', self_anchor_id='top_right',
    )
    next_btn.move(next_placement.x, next_placement.y)

    if has_pages:
        prev_btn.show_btn()
        next_btn.show_btn()
    else:
        prev_btn.hide_btn()
        next_btn.hide_btn()
