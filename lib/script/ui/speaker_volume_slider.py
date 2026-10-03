"""音响音量滑条 - 音响右键 UI 中搜索框上方的音量调节控件。

布局与交互：
  - 与「搜索框 + 搜索歌曲」整行等宽，高度与播放进度条一致（20px）
  - 样式完全来自共享滑条视觉（黑/青/粉三层面板 + 竖向矩形手柄）
  - 20 个小刻度，拖动时吸附到 1/20 档，形成颗粒手感
  - 点击或拖动发布 ``MUSIC_VOLUME {'volume': ratio}``，松开时提示当前百分比

本类不再继承 ``QWidget``：比例、拖动与吸附都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``RectSliderControl``）里，真实窗口、透明度
动画与拖动捕获由后端窗口宿主持有。定位由 ``SpeakerControlButtons`` 统一管理。
"""

from __future__ import annotations

from config.config import UI, SPEAKER_SEARCH_UI
from config.scale import scale_px
from lib.core.event.center import get_event_center, Event, EventType
from lib.core.render.visuals import controls
from lib.core.render.visuals.media_panel_visuals import (
    PROGRESS_PANEL_HEIGHT,
    SLIDER_TICK_COUNT,
)
from lib.script.ui.render_bridge import create_control_host
from lib.core.render.layers import Layer
from lib.script.music import get_music_service


DEFAULT_WIDTH = int(SPEAKER_SEARCH_UI.get('input_width', scale_px(160, min_abs=1))) + int(
    SPEAKER_SEARCH_UI.get('button_width', scale_px(80, min_abs=1))
)
DEFAULT_HEIGHT = PROGRESS_PANEL_HEIGHT


def _music_volume_ratio() -> float | None:
    """读取当前音乐音量（0.0-1.0）；服务不可用时返回 None。"""
    try:
        service = get_music_service()
        return max(0.0, min(1.0, float(service.get_volume_percent()) / 100.0))
    except Exception:
        return None


def snap_ratio(ratio: float) -> float:
    """把任意比例吸附到 1/20 档（滑条颗粒手感）。"""
    return controls.snap_slider_ratio(ratio, SLIDER_TICK_COUNT)


class SpeakerVolumeSlider:
    """音响右键 UI 的音量滑条（全局单例的附属控件）。"""

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        self._control = controls.RectSliderControl(
            width=int(width),
            height=int(height),
            ticks=SLIDER_TICK_COUNT,
            paint_layer=int(Layer.PET_UI),
            opacity_scale=controls.ui_opacity_scale,
        )
        self._event_center = get_event_center()
        self._last_local_x = 0.0
        self._tick_counter = 0

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
        self._host._description = '拖动调节音乐音量'

        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

    # ==================================================================
    # 状态视图（测试与内部逻辑读取的稳定入口）
    # ==================================================================

    @property
    def ratio(self) -> float:
        return self._control.ratio

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
    def _ratio(self) -> float:
        return self._control.ratio

    @_ratio.setter
    def _ratio(self, value: float) -> None:
        self._control.ratio = float(value)

    @property
    def _dragging(self) -> bool:
        return self._control.dragging

    @_dragging.setter
    def _dragging(self, value: bool) -> None:
        self._control.dragging = bool(value)

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

    def move(self, x: int, y: int) -> None:
        self._host.move_to(int(x), int(y))

    def track_rect(self):
        return self._control.track_rect()

    def ratio_from_x(self, x: float) -> float:
        """把控件内横坐标换算成 0.0-1.0 的比例。"""
        return self._control.ratio_from_x(x)

    def _sync_from_service(self) -> None:
        ratio = _music_volume_ratio()
        if ratio is None:
            return
        snapped, changed = self._control.set_ratio(ratio)
        if changed:
            self._host.update()

    def set_ratio(self, ratio: float, *, emit: bool = False, notify: bool = False) -> None:
        snapped, changed = self._control.set_ratio(ratio)
        if changed:
            self._host.update()
        if emit and changed:
            self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'volume': snapped}))
        if notify:
            self._publish_volume_bubble()

    def _publish_volume_bubble(self) -> None:
        ratio = _music_volume_ratio()
        percent = int(round((self._control.ratio if ratio is None else ratio) * 100))
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'音量 {percent}%',
            'min': 0,
        }))

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def fade_in(self) -> None:
        if self._control.visible:
            return
        self._control.visible = True
        self._sync_from_service()
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

    def _on_tick(self, event: Event) -> None:
        if not self._control.visible or self._control.dragging:
            return
        self._tick_counter += 1
        if self._tick_counter < 20:
            return
        self._tick_counter = 0
        self._sync_from_service()

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                           self._on_clickthrough_toggle)
        except Exception:
            pass
        self._host.cleanup()

    def close(self) -> None:
        self._host.cleanup()

    # ==================================================================
    # 绘制
    # ==================================================================

    def _build_visual(self):
        return self._control.build_visual()

    def _paint_batch(self):
        return self._control.build_visual().batch

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def _on_pointer(self, event) -> None:
        if event.button != controls.BUTTON_LEFT:
            return controls.PointerClick()
        self._last_local_x = event.local.x
        self._control.dragging = True
        snapped, changed = self._control.set_ratio(self.ratio_from_x(event.local.x))
        if changed:
            self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'volume': snapped}))
        self._host.update()
        return controls.PointerClick(particle_id='click')

    def _on_pointer_move(self, event) -> None:
        if not self._control.dragging:
            return
        self._last_local_x = event.local.x
        snapped, changed = self._control.set_ratio(self.ratio_from_x(event.local.x))
        if changed:
            self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'volume': snapped}))
        self._host.update()

    def _on_pointer_release(self) -> None:
        if not self._control.dragging:
            return
        self._control.dragging = False
        self.set_ratio(self.ratio_from_x(self._last_local_x), emit=True, notify=True)


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "SpeakerVolumeSlider",
    "snap_ratio",
]
