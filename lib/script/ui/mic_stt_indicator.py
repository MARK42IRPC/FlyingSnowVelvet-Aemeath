"""语音识别状态指示器。

本类不再继承 ``QWidget``：可见性、悬停半径判定与"鼠标离开多久后收起"都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``MicSttControl``）里，真实窗口由后端
窗口宿主持有。控件自身只负责事件订阅、把几何算清楚，以及把绘制批次交给宿主。
"""
from __future__ import annotations

import time

from config.config import UI
from lib.core.render.visuals import controls
from lib.core.render.visuals.anchors import get_anchor_point
from lib.core.render.visuals.application_visuals import build_mic_stt_indicator_visual
from lib.script.ui.render_bridge import (
    create_control_host,
    place_at_point,
    pointer_position,
    screen_rect_for_point,
    widget_global_rect,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer


class MicSttIndicator:
    """监听语音识别状态的小方块，点击可停止监听。"""

    SIZE = scale_px(24, min_abs=18)

    def __init__(self, pet_window):
        self._pet_window = pet_window
        self._event_center = get_event_center()
        self._margin = scale_px(4, min_abs=3)
        self._extra_offset_y = scale_px(20, min_abs=20)
        self._description = "语音识别中，点击可停止"

        self._control = controls.MicSttControl(
            size=self.SIZE,
            hover_radius=scale_px(120, min_abs=90),
            hide_delay=2.0,
            paint_layer=int(Layer.PET_UI),
            opacity_scale=controls.ui_opacity_scale,
        )

        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_fade_out_finished=self._on_fade_out_finished,
            layer=Layer.PET_UI,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=UI['ui_fade_duration'],
            pointing_cursor=True,
        )
        # 指示器是固定边长的方块：窗口尺寸必须先定下来，位置与命中判定才算得对。
        self._host.apply_size(self.SIZE, self.SIZE)
        self._host._description = self._description

        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_state_change)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        self._host.hide()

    # ------------------------------------------------------------------
    # 描述状态（测试与内部逻辑读取的稳定入口）
    # ------------------------------------------------------------------
    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _listening(self) -> bool:
        return self._control.listening

    @_listening.setter
    def _listening(self, value: bool) -> None:
        self._control.listening = bool(value)

    @property
    def _speech_active(self) -> bool:
        return self._control.speech_active

    @_speech_active.setter
    def _speech_active(self, value: bool) -> None:
        self._control.speech_active = bool(value)

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_frame(self, event: Event) -> None:
        if self._control.listening:
            self._update_position()
            self._update_visibility_by_cursor()

    def _on_state_change(self, event: Event) -> None:
        listening = bool(event.data.get('is_listening'))
        self._control.speech_active = bool(event.data.get('speech_active'))
        status = str(event.data.get('status', '') or '').strip()
        if status:
            self._description = f"语音识别({status})，点击停止"
            self._host._description = self._description

        if listening:
            self._control.listening = True
            self._update_position()
            self._control.mark_pointer_inside(pointer_position())
            self._show_indicator()
            self._host.update()
        else:
            self._control.listening = False
            self._hide_indicator()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    def _on_fade_out_finished(self) -> None:
        if not self._control.visible:
            self._host.hide()

    def _on_pointer(self, event):
        """左键点击 → 要求停止语音识别。"""
        return self._control.click_intent(event)

    # ------------------------------------------------------------------
    # 可视化
    # ------------------------------------------------------------------
    def _show_indicator(self) -> None:
        if self._control.visible:
            return
        self._control.visible = True
        self._host.show()
        self._animate(1.0)

    def _hide_indicator(self) -> None:
        if not self._control.visible:
            return
        self._control.visible = False
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=UI['ui_fade_duration'],
            fade_out=not self._control.visible,
        )

    def _update_position(self) -> None:
        if self._pet_window is None:
            return
        rect = widget_global_rect(self._pet_window)
        size = (self.width(), self.height())
        anchor = get_anchor_point(rect, 'top_left')
        placement = place_at_point(
            size,
            anchor,
            screen_rect_for_point(point=anchor, fallback_widget=self._host),
            target_anchor_id='top_left',
            self_anchor_id='top_left',
            offset_x=self._margin,
            offset_y=-size[1] - self._margin + self._extra_offset_y,
        )
        self._host.move_to(placement.x, placement.y)

    def _update_visibility_by_cursor(self) -> None:
        action = self._control.update_hover(
            pointer_position(),
            self._host.geometry_rect(),
            now=time.monotonic(),
        )
        if action == controls.HOVER_SHOW:
            self._show_indicator()
        elif action == controls.HOVER_HIDE:
            self._hide_indicator()

    # ------------------------------------------------------------------
    # 绘制与生命周期
    # ------------------------------------------------------------------
    def _paint_batch(self):
        return build_mic_stt_indicator_visual(
            speech_active=self._control.speech_active,
        ).batch

    def update(self) -> None:
        self._host.update()

    def hide(self) -> None:
        self._host.stop_animation()
        self._control.visible = False
        self._host.hide()

    def close(self) -> None:
        """取消订阅并释放后端窗口。"""
        for event_type, handler in (
            (EventType.FRAME, self._on_frame),
            (EventType.MIC_STT_STATE_CHANGE, self._on_state_change),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            try:
                self._event_center.unsubscribe(event_type, handler)
            except Exception:
                pass
        self._host.cleanup()
