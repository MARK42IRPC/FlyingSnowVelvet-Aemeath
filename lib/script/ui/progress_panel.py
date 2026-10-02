"""播放进度条 - 显示当前音乐播放进度

功能：
  - 左下锚点对齐播放列表左上锚点
  - 绘制风格与其它 UI 一致：2px 黑色外框 + 2px 青色中框 + 黑色内背景
  - 固定宽度 240px，其中 174px 为进度滑块区、5px 黑色分隔线，右侧 57px 显示剩余时间

交互：
  - 按住滑块拖动或点击位置可修改当前播放进度
  - 已播放部分为灰色填充，其余为黑色背景

本类不再继承 ``QWidget``：进度、剩余时长、拖动与命中区域都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``MediaProgressControl``）里，真实窗口与透明度动画由后端窗口宿主持有。
"""
from __future__ import annotations

from config.config import UI, FONT
from lib.core.render.visuals import controls
from lib.core.render.visuals.types import Rect
from lib.script.ui.render_bridge import (
    create_control_host,
    digit_font as get_digit_font,
    pointer_position,
    screen_rect_for_point,
    text_metrics as QtTextMetrics,
    ui_font as get_ui_font,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer


# ── 布局常量 ──────────────────────────────────────────────────────
_WIDTH  = scale_px(240, min_abs=1)  # 固定宽度（px）
_HEIGHT = scale_px(20, min_abs=1)   # 固定高度（px），与 playlist 中的一致
_GAP    = scale_px(2, min_abs=1)    # 距播放列表上沿的间距


class ProgressPanel:
    """播放进度条（全局单例）。"""

    def __init__(self) -> None:
        self._font = get_ui_font(FONT['ui_size'] - 1)
        self._font.setBold(True)
        self._time_font = get_digit_font(FONT['ui_size'] - 1)
        self._text_metrics = QtTextMetrics(self._font, self._time_font)

        self._control = controls.MediaProgressControl(
            self._text_metrics,
            width=_WIDTH,
            height=_HEIGHT,
            gap=_GAP,
            paint_layer=int(Layer.PANEL),
            opacity_scale=controls.ui_opacity_scale,
        )

        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_pointer_release=self._on_pointer_release,
            on_fade_out_finished=self._on_fade_out_done,
            layer=Layer.PANEL,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=UI['ui_fade_duration'],
        )
        self._host.apply_size(_WIDTH, _HEIGHT)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status)
        self._event_center.subscribe(EventType.MUSIC_PROGRESS, self._on_music_progress)
        self._event_center.subscribe(EventType.MUSIC_SONG_END, self._on_song_end)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    # ==================================================================
    # 状态视图（测试与内部逻辑读取的稳定入口）
    # ==================================================================
    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _progress(self) -> float:
        return self._control.progress

    @_progress.setter
    def _progress(self, value: float) -> None:
        self._control.progress = value

    @property
    def _remaining(self) -> int:
        return self._control.remaining

    @_remaining.setter
    def _remaining(self, value: int) -> None:
        self._control.remaining = value

    @property
    def _dragging(self) -> bool:
        return self._control.dragging

    @_dragging.setter
    def _dragging(self, value: bool) -> None:
        self._control.dragging = bool(value)

    @property
    def _drag_progress(self) -> float:
        return self._control.drag_progress

    @_drag_progress.setter
    def _drag_progress(self, value: float) -> None:
        self._control.drag_progress = value

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    def isVisible(self) -> bool:
        return bool(self._host.isVisible())

    # ==================================================================
    # 公开接口
    # ==================================================================

    def show_panel(self) -> None:
        """显示进度条。"""
        if self._control.visible:
            self._host.update()
            return
        self._control.visible = True
        self._host.show()
        self._animate(1.0)

    def hide_panel(self) -> None:
        """隐藏进度条。"""
        if not self._control.visible:
            return
        self._control.visible = False
        self._animate(0.0)

    def set_position_below_playlist(self, playlist_rect) -> None:
        """左下锚点对齐播放列表的左上锚点（进度条在播放列表上方）。"""
        cursor = pointer_position()
        screen = screen_rect_for_point(point=cursor, fallback_widget=self._host)
        placement = self._control.placement(_core_rect(playlist_rect), screen)
        self._host.move_to(placement.x, placement.y)

    # ==================================================================
    # 私有：进度更新
    # ==================================================================

    def _on_music_progress(self, event: Event) -> None:
        """处理播放进度事件（由音乐管理器响应请求后发布）。"""
        if self._control.dragging or not self._control.visible:
            return
        self._control.apply_progress(
            event.data.get('progress', 0.0),
            event.data.get('remaining', 0),
        )
        self._host.update()

    def _on_song_end(self, event: Event) -> None:
        """处理歌曲播放结束事件。"""
        self._control.reset_progress()
        self._host.update()

    # ==================================================================
    # 私有：动画与命中区
    # ==================================================================

    def _animate(self, target: float) -> None:
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=UI['ui_fade_duration'],
            fade_out=not self._control.visible,
        )

    def _on_fade_out_done(self) -> None:
        if not self._control.visible:
            self._host.hide()

    # ==================================================================
    # 鼠标事件（由宿主翻译成中立事件）
    # ==================================================================

    def _on_pointer(self, event):
        if self._control.dragging:
            self._control.update_drag(event.local.x)
            self._host.update()
            return controls.PointerClick()

        self._control.begin_drag(event.local.x)
        self._host.update()
        return controls.PointerClick()

    def _on_pointer_release(self) -> None:
        if not self._control.dragging:
            return
        progress = self._control.end_drag()
        self._event_center.publish(Event(EventType.MUSIC_SEEK, {
            'progress': progress,
        }))
        self._host.update()

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_frame(self, event: Event) -> None:
        """帧事件处理：仅负责位置跟随播放列表。"""
        if not self._control.visible:
            return
        from lib.script.ui.playlist_panel import get_playlist_panel
        playlist_panel = get_playlist_panel()
        if playlist_panel and playlist_panel.is_visible:
            self.set_position_below_playlist(playlist_panel.geometry())
        else:
            self.hide_panel()

    def _on_tick(self, event: Event) -> None:
        """Tick 事件处理：固定节奏请求音乐进度。"""
        if self._control.advance_tick(20):
            self._event_center.publish(Event(EventType.MUSIC_PROGRESS_REQUEST, {}))

    def _on_music_status(self, event: Event) -> None:
        """播放状态变化时更新。"""
        self._control.playing = bool(event.data.get('playing', False))
        self._control.paused = bool(event.data.get('paused', False))

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._host.set_clickthrough(bool(event.data.get('enabled', False)))

    # ==================================================================
    # 绘制与生命周期
    # ==================================================================

    def _paint_batch(self):
        return self._control.build_visual().batch

    def _build_visual(self):
        return self._control.build_visual()

    def update(self) -> None:
        self._host.update()

    def hide(self) -> None:
        self._host.stop_animation()
        self._control.visible = False
        self._host.hide()

    def close(self) -> None:
        self._host.cleanup()

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status)
            self._event_center.unsubscribe(EventType.MUSIC_PROGRESS, self._on_music_progress)
            self._event_center.unsubscribe(EventType.MUSIC_SONG_END, self._on_song_end)
            self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        except Exception:
            pass


def _core_rect(value) -> Rect:
    """把核心 ``Rect`` 或 Qt 取值对象统一成核心 ``Rect``。"""
    if isinstance(value, Rect):
        return value
    return Rect(
        float(value.x()) if callable(getattr(value, "x", None)) else float(getattr(value, "x", 0)),
        float(value.y()) if callable(getattr(value, "y", None)) else float(getattr(value, "y", 0)),
        float(value.width()) if callable(getattr(value, "width", None)) else float(getattr(value, "width", 0)),
        float(value.height()) if callable(getattr(value, "height", None)) else float(getattr(value, "height", 0)),
    )


def _contains(rect: Rect, point) -> bool:
    if point is None:
        return False
    return bool(
        rect.x <= point.x < rect.x + rect.width
        and rect.y <= point.y < rect.y + rect.height
    )


# ── 全局单例 ──────────────────────────────────────────────────────
_instance: 'ProgressPanel | None' = None


def get_progress_panel() -> 'ProgressPanel | None':
    """获取全局进度条（未初始化时返回 None）。"""
    return _instance


def init_progress_panel() -> 'ProgressPanel':
    """初始化并返回全局进度条（必须在 Qt 主线程中调用）。"""
    global _instance
    if _instance is None:
        _instance = ProgressPanel()
    return _instance


def cleanup_progress_panel():
    """释放全局进度条资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.cleanup()
        except Exception:
            pass
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None
