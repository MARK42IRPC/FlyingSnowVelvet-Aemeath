"""扬声器控制按钮的「壳」：按钮基类与无 Qt 的取值/坐标助手。

本模块从 `speaker_control_buttons.py` 切出 档位 3 迁移后的按钮基类
`SpeakerControlButton`（自绘按钮的共享状态机：悬停/按下/淡入淡出/指针命中/动作手势），
以及它依赖的 9 个纯函数助手（字号规格、音乐服务快照、音量气泡、锚点换算）。
常量 `_BTN_WIDTH` / `_BTN_HEIGHT` / `_BUTTON_LAYER` / `_FADE_MS` / `_UI_OPACITY_SCALE`
只被基类与助手使用，随之一起下沉。

行级等价：`SpeakerControlButton` 的方法体与这些助手函数与前一份逐字节相同，只是换了宿主
模块。`speaker_control_buttons.py` 反向 `from ... import` 这些名字，既有导入面不变
（`playlist_panel.py` 仍从原模块取 `_BTN_*` 常量，`lib/script/ui/__init__.py` 的
`SpeakerControlButton` 懒导出仍指向原模块）。

本模块不 `import PyQt5`，按 34.2 节规则不登记 `frozen_ui_qt_importers`；但它
`import lib.script.music`，须登记第 33 节的 ui -> 产品包耦合清单。
"""

from __future__ import annotations

from config.config import SPEAKER_SEARCH_UI
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.layers import Layer
from lib.core.render.visuals import controls
from lib.core.render.visuals.speaker_band_visuals import BAND_SLIDER_GAP
from lib.core.render.visuals.types import FontSpec, Point
from lib.script.music import get_music_service
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.ui import render_bridge


_BTN_WIDTH        = scale_px(40, min_abs=1)  # 图标按钮宽度（播放/暂停、下一曲）
_BTN_HEIGHT       = scale_px(32, min_abs=1)  # 所有按钮统一高度
_BTN_PLAYLIST_W   = scale_px(80, min_abs=1)  # 播放列表按钮宽度（与搜索按钮等宽）
_SEARCH_DIALOG_W  = SPEAKER_SEARCH_UI.get('input_width', scale_px(160, min_abs=1)) + SPEAKER_SEARCH_UI.get('button_width', scale_px(80, min_abs=1))
_SEARCH_DIALOG_H  = int(SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1)))  # 搜索框高度
_VOLUME_SLIDER_GAP = scale_px(2, min_abs=1)  # 滑条与搜索框/按钮之间的间隙
_BAND_SLIDER_GAP  = BAND_SLIDER_GAP         # 频段滑条与菜单主体之间的水平间隙

_BUTTON_LAYER = int(Layer.PET_UI)
_FADE_MS = 200
_UI_OPACITY_SCALE = controls.ui_opacity_scale


def _font_spec(font) -> FontSpec:
    """把后端 UI 字体对象压成后端无关的 ``FontSpec``（与旧 paint 逐字一致）。"""
    return FontSpec(font.family(), font.pixelSize(), font.bold())


def _safe_music_service():
    try:
        return get_music_service()
    except Exception:
        return None


def _music_is_playing() -> bool:
    service = _safe_music_service()
    if service is None:
        return False
    try:
        return bool(service.is_playing() and not service.is_paused())
    except Exception:
        return False


def _music_login_snapshot(
    fallback_logged_in: bool = False,
    fallback_provider: str = 'netease',
) -> tuple[bool, str]:
    service = _safe_music_service()
    logged_in = bool(fallback_logged_in)
    provider = str(fallback_provider or 'netease')
    if service is not None:
        try:
            logged_in = bool(service.is_logged_in())
        except Exception:
            pass
        try:
            provider = str(service.provider_name or provider)
        except Exception:
            pass
    return logged_in, provider.strip().lower()


def _music_provider_mode_label(default: str = '音乐模式') -> str:
    service = _safe_music_service()
    if service is None:
        return default
    try:
        return str(service.provider_mode_label)
    except Exception:
        return default


def _music_play_mode(default: str = 'list_loop') -> str:
    service = _safe_music_service()
    if service is None:
        return default
    try:
        return str(service.play_mode())
    except Exception:
        return default


def _music_volume_percent(default: int = 0) -> int:
    service = _safe_music_service()
    if service is None:
        return default
    try:
        return int(service.get_volume_percent())
    except Exception:
        return default


def _publish_volume_bubble(event_center) -> None:
    vol = _music_volume_percent()
    event_center.publish(Event(EventType.INFORMATION, {
        'text': f'\u97f3\u91cf {vol}%',
        'min': 0,
    }))


def _anchor_point_of(widget, anchor_id: str) -> Point:
    """控件锚点的屏幕坐标（核心 ``Point``）：本地锚点 + 宿主屏幕原点。"""
    rect = render_bridge.widget_global_rect(widget)
    local = render_bridge.local_anchor_point(anchor_id, widget.width(), widget.height())
    return Point(rect.x + local.x, rect.y + local.y)


class SpeakerControlButton:
    """音响控制按钮基类（描述 + 宿主装配）。

    按钮样式：2px 黑色外框 + 2px 灰白色中框 + 棕色背景 + 黑色几何图标 / 文字。
    """

    #: 几何图标名（``pause`` / ``play`` / ``next_track``）；文字按钮保持 ``None``。
    _GLYPH = None
    #: 文字按钮的静态标签；动态文案由 ``label()`` 覆盖。
    _TEXT = ''

    def __init__(self, width: int = _BTN_WIDTH, height: int = _BTN_HEIGHT):
        self._width = int(width)
        self._height = int(height)
        self._description = ''   # 由各子类覆盖
        self._event_center = get_event_center()
        self._font = render_bridge.ui_font()
        self._font.setBold(True)

        self._control = controls.SpeakerActionButtonControl(
            width=self._width,
            height=self._height,
            glyph=self._GLYPH,
            text=self.label(),
            fade_duration_ms=_FADE_MS,
            paint_layer=_BUTTON_LAYER,
            opacity_scale=_UI_OPACITY_SCALE,
        )

        self._host = render_bridge.create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_pointer_move=self._on_pointer_move,
            on_pointer_release=self._on_pointer_release,
            on_hover_changed=self._on_hover_changed,
            on_fade_out_finished=self._on_fade_out_finished,
            layer=Layer.PET_UI,
            fade_duration_ms=_FADE_MS,
            fade_out_duration_ms=_FADE_MS,
            # 按钮不抢键盘焦点：点到它们时搜索输入框必须保住焦点。
            accepts_focus=False,
            pointing_cursor=True,
        )
        self._host.apply_size(self._width, self._height)
        self._host._description = self._description

        #: 指针是否还在按钮矩形内。Qt 只在按下时投递 mouseMoveEvent，因此这就是
        #: 迁移前 ``self.rect().contains(event.pos())`` 的等价判定。
        self._pointer_inside = False
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    # ── 子类接口 ───────────────────────────────────────────────────
    def label(self) -> str:
        """要画的文字（文字按钮覆盖；几何图标按钮不读它）。"""
        return str(self._TEXT or '')

    def on_clicked(self):
        """子类重写此方法实现点击逻辑"""
        pass

    # ── 状态视图 ───────────────────────────────────────────────────
    @property
    def _visible(self) -> bool:
        return self._control.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._control.visible = bool(value)

    @property
    def _hovered(self) -> bool:
        return self._control.hovered

    @_hovered.setter
    def _hovered(self, value: bool) -> None:
        self._control.hovered = bool(value)

    @property
    def _pressed(self) -> bool:
        return self._control.pressed

    @_pressed.setter
    def _pressed(self, value: bool) -> None:
        self._control.pressed = bool(value)

    @property
    def _label_font(self):
        return self._font

    def get_anchor_point(self, anchor_id: str) -> Point:
        """获取指定锚点在本控件内的位置（核心 ``Point``）。"""
        return render_bridge.local_anchor_point(anchor_id, self.width(), self.height())

    def paint_batch(self):
        """当前帧的绘制批次（测试与调试可见；宿主每帧调用 ``_paint_batch``）。"""
        self._control.text = self.label()
        return self._control.build_visual(_font_spec(self._font)).batch

    # ── 绘制 / 指针 ────────────────────────────────────────────────
    def _paint_batch(self):
        return self.paint_batch()

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
        if self._finish_action_press(commit=self._pointer_inside):
            self.on_clicked()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._control.clickthrough = bool(event.data.get('enabled', False))
        self._host.set_clickthrough(self._control.clickthrough)

    # ── 淡入 / 淡出 ────────────────────────────────────────────────
    def fade_in(self):
        if self._control.visible:
            return
        self._control.visible = True
        self._host.show()
        self._animate(1.0)

    def fade_out(self):
        self._cancel_action_press()
        if not self._control.visible:
            return
        self._control.visible = False
        self._animate(0.0, fade_out=True)

    def _animate(self, target: float, *, fade_out: bool = False):
        self._host.fade_to(
            self._control.scaled_opacity(target),
            duration_ms=_FADE_MS,
            fade_out=fade_out,
        )

    def _on_fade_out_finished(self) -> None:
        if not self._control.visible:
            self._host.hide()

    # ── 尺寸 / 位置 / 可见性视图（宿主转发）───────────────────────
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
        self._control.visible = False
        self._host.hide()

    def close(self) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(
                EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle
            )
        except Exception:
            pass
        self._host.cleanup()
