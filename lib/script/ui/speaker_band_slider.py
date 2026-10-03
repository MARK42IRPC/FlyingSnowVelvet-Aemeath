"""音响响应频段滑条 - 音响右键 UI 右侧的竖向滑条。

布局与交互：
  - 贴在「搜索框 + 搜索歌曲」整行右侧，上下与菜单上半部分对齐
    （顶端对齐第一行按钮，底端对齐搜索框下沿）
  - 外观来自共享竖向滑条视觉（黑/青/粉三层面板 + 单个横向块 + 小刻度）
  - 只有一个块：按住拖动，块的位置就是频段的中心频率，频段固定为中心 ±10Hz，
    中心按 10Hz 吸附，所以拖出来的总是 10Hz 整数倍的区间
  - 松手时提示当前频段

本类不再继承 ``QWidget``：频段读数、拖动与命中都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``BandSliderControl``）里，真实窗口、透明度
动画与拖动捕获由后端窗口宿主持有。位置由 ``SpeakerControlButtons`` 统一管理。
"""

from __future__ import annotations

from config.config import SPEAKER_SEARCH_UI, UI
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, Event, EventType
from lib.core.render.visuals import controls
from lib.core.render.visuals.speaker_band_visuals import (
    BAND_SLIDER_WIDTH,
    band_hit_test,
    band_ratio_at,
)
from lib.core.render.visuals.speaker_visuals import SPEAKER_SEARCH_Y
from lib.script.ui.render_bridge import create_control_host
from lib.core.speaker_band import (
    band_from_center_ratio,
    band_label,
    default_band,
    get_speaker_band,
    set_speaker_band,
)
from lib.core.render.layers import Layer


DEFAULT_WIDTH = BAND_SLIDER_WIDTH
#: 默认高度 = 菜单上半部分（两行按钮 + 音量滑条 + 搜索框）的总高。
DEFAULT_HEIGHT = SPEAKER_SEARCH_Y + int(SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1)))


class SpeakerBandSlider:
    """音响右键 UI 的动感响应频段滑条（竖向、单块，全局单例的附属控件）。"""

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        self._control = controls.BandSliderControl(
            width=int(width),
            height=int(height),
            band=default_band(),
            paint_layer=int(Layer.PET_UI),
            opacity_scale=controls.ui_opacity_scale,
        )
        self._event_center = get_event_center()
        self._last_local_y = 0.0

        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_pointer_move=self._on_pointer_move,
            on_pointer_release=self._on_pointer_release,
            on_fade_out_finished=self._on_fade_out_finished,
            layer=Layer.PET_UI,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=UI['ui_fade_duration'],
            capture_on_press=True,
            pointing_cursor=True,
        )
        self._host.apply_size(int(width), int(height))
        self._host._description = TOOLTIPS.get('speaker_band_slider', '拖动调节音响的动感响应频段')

        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

    # ==================================================================
    # 状态视图
    # ==================================================================
    @property
    def band(self) -> tuple[float, float]:
        return self._control.band

    @property
    def is_visible(self) -> bool:
        return self._control.visible

    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _dragging(self) -> str:
        return self._control.dragging

    @_dragging.setter
    def _dragging(self, value) -> None:
        self._control.dragging = str(value or '')

    @property
    def bound_speaker(self):
        return self._control.speaker

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    def x(self) -> int:
        return self._host.x()

    def y(self) -> int:
        return self._host.y()

    def isVisible(self) -> bool:
        return bool(self._host.isVisible())

    def set_speaker(self, speaker) -> None:
        """绑定（或解绑）当前锚定的音响，并同步它的响应频段。"""
        self._control.speaker = speaker
        self._control.band = self._read_band()
        self.invalidate()

    def apply_geometry(self, x: int, y: int, height: int) -> None:
        """由按钮组给出的位置与高度（上下对齐菜单上半部分）。"""
        target_height = max(1, int(height))
        self._host.apply_size(self.width(), target_height)
        if self._control.height != target_height:
            self._control.height = float(target_height)
        self._host.move_to(int(x), int(y))

    def invalidate(self) -> None:
        """频段或尺寸变化后丢弃缓存的绘制批次。"""
        self._host.update()

    def _read_band(self) -> tuple[float, float]:
        speaker = self._control.speaker
        if speaker is None:
            return default_band()
        try:
            return get_speaker_band(speaker.backend_id, speaker.instance_id)
        except Exception:
            return default_band()

    def _write_band(self) -> None:
        speaker = self._control.speaker
        if speaker is None:
            return
        try:
            set_speaker_band(
                speaker.backend_id,
                speaker.instance_id,
                self._control.band[0],
                self._control.band[1],
            )
        except Exception:
            pass

    def _publish_band_bubble(self) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'响应频段 {band_label(self._control.band)}',
            'min': 0,
        }))

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================
    def fade_in(self) -> None:
        if self._control.visible:
            return
        self._control.visible = True
        self._control.band = self._read_band()
        self.invalidate()
        self._host.show()
        self._animate(1.0)

    def fade_out(self) -> None:
        if not self._control.visible:
            return
        self._control.visible = False
        self._animate(0.0, fade_out=True)

    def _animate(self, target: float, *, fade_out: bool = False) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=UI['ui_fade_duration'],
            fade_out=fade_out,
        )

    def _on_fade_out_finished(self) -> None:
        if not self._control.visible:
            self._host.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                           self._on_clickthrough_toggle)
        except Exception:
            pass
        self._host.cleanup()

    def close(self) -> None:
        self._host.cleanup()

    def deleteLater(self) -> None:
        self._host.cleanup()

    # ==================================================================
    # 绘制
    # ==================================================================
    def _build_visual(self):
        return self._control.build_visual()

    def _ensure_visual(self):
        return self._control.build_visual()

    def _paint_batch(self):
        return self._control.build_visual().batch

    # ==================================================================
    # 鼠标交互
    # ==================================================================
    def _apply_y(self, y: float) -> None:
        """把块拖到指针所在的频率：频段随之变成「中心 ±10Hz」。"""
        if not self._control.dragging:
            return
        visual = self._control.build_visual()
        ratio = band_ratio_at(visual.track_rect, y)
        band = band_from_center_ratio(ratio)
        if band == self._control.band:
            return
        self._control.band = band
        self._write_band()
        self.invalidate()

    def _on_pointer(self, event):
        if event.button != controls.BUTTON_LEFT:
            return controls.PointerClick()
        visual = self._control.build_visual()
        action = band_hit_test(
            visual.track_rect,
            visual.center_rect,
            event.local.x,
            event.local.y,
        )
        if not action:
            return controls.PointerClick()
        self._last_local_y = event.local.y
        self._control.dragging = str(action)
        self._apply_y(event.local.y)
        return controls.PointerClick(particle_id='click')

    def _on_pointer_move(self, event) -> None:
        if not self._control.dragging:
            return
        self._last_local_y = event.local.y
        self._apply_y(event.local.y)

    def _on_pointer_release(self) -> None:
        if not self._control.dragging:
            return
        self._apply_y(self._last_local_y)
        self._control.dragging = ''
        self._publish_band_bubble()


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "SpeakerBandSlider",
]
