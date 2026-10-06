"""音响控制按钮族 - 暂停/播放、下一曲、登录、平台模式、搜索优先级、播放列表等。

本模块不再继承 ``QWidget``：按钮形状、悬停/按下状态、几何图标与文字绘制都在描述层
（``lib/core/render/visuals/controls.py`` 的 ``SpeakerActionButtonControl``，绘制事实源是
``application_visuals.build_speaker_action_button_visual``），真实窗口、透明度动画与指针
翻译由后端窗口宿主持有。族内六个按钮的落位仍走 ``visuals/layout.py`` 的共享解算。

对外接口保持迁移前的形状（``fade_in`` / ``fade_out`` / ``move`` / ``width`` / ``height`` /
``x`` / ``y`` / ``isVisible`` / ``close`` / ``cleanup``），族内跟随者继续用
``widget_global_rect()`` 与 ``isVisible()`` 测量它们。
"""

from __future__ import annotations

from config.config import SPEAKER_SEARCH_UI
from lib.core.render.visuals.speaker_band_visuals import BAND_SLIDER_GAP
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.layers import Layer
from lib.core.render.visuals import controls
from lib.core.render.visuals.types import FontSpec, Point, Rect, coerce_point
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.music import get_music_service
from lib.script.ui import render_bridge
from lib.script.ui.speaker_band_slider import (
    DEFAULT_HEIGHT as _BAND_SLIDER_HEIGHT,
    DEFAULT_WIDTH as _BAND_SLIDER_WIDTH,
    SpeakerBandSlider,
)
from lib.script.ui.speaker_volume_slider import (
    DEFAULT_HEIGHT as _VOLUME_SLIDER_HEIGHT,
    SpeakerVolumeSlider,
)

# ── 尺寸 ──────────────────────────────────────────────────────────────
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


class PlayPauseButton(SpeakerControlButton):
    """暂停/播放按钮 - 使用几何形状绘制"""

    _GLYPH = controls.SPEAKER_GLYPH_PLAY

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._playing     = False
        self._description = TOOLTIPS['speaker_play_pause']
        self._host._description = self._description

        # 订阅播放状态变化事件
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)

        # 初始化时主动获取当前播放状态
        self._sync_playing_state()

    @property
    def _glyph(self) -> str:
        return (
            controls.SPEAKER_GLYPH_PAUSE
            if self._playing
            else controls.SPEAKER_GLYPH_PLAY
        )

    def _sync_playing_state(self):
        """从音乐服务读取当前播放状态。"""
        self.set_playing(_music_is_playing())

    def set_playing(self, playing: bool):
        """设置播放状态，更新图标"""
        if self._playing != playing:
            self._playing = playing
            self._control.glyph = self._glyph
            self.update()

    def _on_status_change(self, event: Event):
        """处理播放状态变化事件"""
        playing = event.data.get('playing', False)
        self.set_playing(playing)

    def on_clicked(self):
        """切换播放/暂停状态"""
        # 发布播放控制事件，不本地更新状态，由事件中心广播回来更新
        self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {
            'playing': not self._playing
        }))

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)
        except Exception:
            pass
        super().cleanup()


class NextTrackButton(SpeakerControlButton):
    """下一曲按钮 - 使用几何形状绘制"""

    _GLYPH = controls.SPEAKER_GLYPH_NEXT_TRACK

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._description = TOOLTIPS['speaker_next']
        self._host._description = self._description

    def on_clicked(self):
        """播放下一曲"""
        # 发布下一曲事件
        self._event_center.publish(Event(EventType.MUSIC_NEXT_TRACK, {}))


class MusicLoginButton(SpeakerControlButton):
    """音乐平台登录按钮（80px 文字按钮）。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._logged_in = False
        self._provider = 'netease'
        self._description = TOOLTIPS['speaker_music_login']
        self._host._description = self._description
        self._event_center.subscribe(EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status_change)
        self._sync_login_state()

    def _sync_login_state(self) -> None:
        self._logged_in, self._provider = _music_login_snapshot(
            fallback_provider=self._provider,
        )

    def _on_login_status_change(self, event: Event) -> None:
        logged_in, provider = _music_login_snapshot(
            fallback_logged_in=bool(event.data.get('logged_in', False)),
            fallback_provider=str(event.data.get('provider') or self._provider or 'netease'),
        )
        if logged_in != self._logged_in or provider != self._provider:
            self._logged_in = logged_in
            self._provider = provider
            self.update()

    def label(self) -> str:
        provider = getattr(self, '_provider', 'netease')
        if getattr(self, '_logged_in', False):
            label = '已登录'
        elif provider == 'qq':
            label = '登录QQ'
        elif provider == 'kugou':
            label = '登录酷狗'
        else:
            label = '登录音乐'
        return label

    def on_clicked(self):
        if self._logged_in:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '音乐平台账号已登录',
                'min': 0,
                'max': 60,
            }))
            return
        self._event_center.publish(Event(EventType.MUSIC_LOGIN_REQUEST, {}))

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(
                EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status_change
            )
        except Exception:
            pass
        super().cleanup()


class PlatformModeButton(SpeakerControlButton):
    """Platform music mode switch button."""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._mode_label = "网易模式"
        self._description = TOOLTIPS.get('speaker_platform_mode', '切换当前音乐平台模式')
        self._host._description = self._description
        self._sync_mode_state()

    def _sync_mode_state(self) -> None:
        self._mode_label = _music_provider_mode_label()

    def label(self) -> str:
        return str(getattr(self, '_mode_label', self._TEXT or ''))

    def fade_in(self):
        self._sync_mode_state()
        super().fade_in()

    def on_clicked(self):
        service = get_music_service()
        target_provider = service.cycle_provider(persist=True)
        if target_provider is None:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '音乐平台切换失败',
                'min': 0,
                'max': 80,
            }))
            return

        self._sync_mode_state()
        self.update()
        self._event_center.publish(Event(EventType.MUSIC_LOGIN_STATUS_CHANGE, {
            'logged_in': bool(service.is_logged_in()),
            'profile': {},
            'nickname': '',
        }))

        msg = f'当前{self._mode_label}（已保存）'
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': msg,
            'min': 0,
            'max': 120,
        }))


class PlayModeButton(SpeakerControlButton):
    """播放模式按钮 - 三态切换：单曲循环/列表循环/随机播放。"""

    _MODE_LABELS = {
        'single_loop': '单曲循环',
        'list_loop': '列表循环',
        'random': '随机播放',
    }

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._mode = 'list_loop'
        self._description = TOOLTIPS['speaker_play_mode']
        self._host._description = self._description
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)
        self._sync_mode_state()

    def _sync_mode_state(self) -> None:
        self._mode = _music_play_mode()

    def _on_status_change(self, event: Event) -> None:
        mode = str(event.data.get('play_mode', self._mode))
        if mode != self._mode:
            self._mode = mode
            self.update()

    def label(self) -> str:
        return self._MODE_LABELS.get(
            getattr(self, '_mode', 'list_loop'), self._MODE_LABELS['list_loop']
        )

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_PLAY_MODE_TOGGLE, {}))

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)
        except Exception:
            pass
        super().cleanup()


class SearchPriorityButton(SpeakerControlButton):
    """搜索优先级按钮 - 单曲/歌手/专辑/歌单优先切换。"""

    _FALLBACK_LABELS = ('单曲优先', '歌手优先', '专辑优先', '歌单优先')

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._dialog = None
        self._label_index = 0
        self._label = self._FALLBACK_LABELS[self._label_index]
        self._description = TOOLTIPS.get('speaker_search_priority', '切换搜索优先级')
        self._host._description = self._description

    def set_dialog(self, dialog) -> None:
        self._dialog = dialog
        try:
            self._label = str(dialog.search_priority_label)
        except Exception:
            self._label = self._FALLBACK_LABELS[self._label_index]
        self.update()

    def label(self) -> str:
        fallback = self._FALLBACK_LABELS[getattr(self, '_label_index', 0)]
        return str(getattr(self, '_label', fallback))

    def on_clicked(self):
        if self._dialog is not None:
            try:
                self._label = str(self._dialog.cycle_search_priority())
                self.update()
                return
            except Exception:
                pass
        self._label_index = (self._label_index + 1) % len(self._FALLBACK_LABELS)
        self._label = self._FALLBACK_LABELS[self._label_index]
        self.update()


class PlaylistButton(SpeakerControlButton):
    """
    播放列表按钮（文字按钮，参考主宠物关闭/穿透按钮样式）。

    点击后：
      1. 关闭音响右键搜索 UI
      2. 打开播放列表栏，锚定到当前音响右侧
    """

    _TEXT = '播放列表'

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._dialog      = None   # 由 SpeakerControlButtons 注入
        self._description = TOOLTIPS['speaker_playlist_toggle']
        self._host._description = self._description

    def set_dialog(self, dialog) -> None:
        """注入搜索对话框引用，用于获取当前锚定音响。"""
        self._dialog = dialog

    def on_clicked(self):
        """关闭搜索 UI，打开播放列表栏。"""
        speaker = self._dialog.focused_speaker if self._dialog else None

        # 关闭音响右键搜索 UI（含结果框和控制按钮）
        from lib.script.ui.speaker_search_dialog import get_speaker_search_dialog
        dlg = get_speaker_search_dialog()
        if dlg:
            dlg.toggle(None)

        # 打开播放列表栏（懒初始化单例）
        if speaker:
            from lib.script.ui.playlist_panel import get_playlist_panel, init_playlist_panel
            # 初始化播放列表
            panel = get_playlist_panel() or init_playlist_panel()
            panel.show_for(speaker)


class HistoryQueueButton(SpeakerControlButton):
    """一键历史按钮 - 将 history.json 中歌曲批量追加到播放队列末尾。"""

    _TEXT = '一键历史'

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._description = TOOLTIPS['speaker_history_queue']
        self._host._description = self._description

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_HISTORY, {}))


class ClearQueueButton(SpeakerControlButton):
    """清空列表按钮 - 停止播放并清空当前队列。"""

    _TEXT = '清空列表'

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._description = TOOLTIPS.get('speaker_clear_queue', '清空列表')
        self._host._description = self._description

    def on_clicked(self):
        get_music_service().clear_queue()


class LocalQueueButton(SpeakerControlButton):
    """一键本地按钮 - 清空队列并载入本地音乐文件夹中的全部歌曲。"""

    _TEXT = '一键本地'

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._description = TOOLTIPS.get('speaker_local_queue', '加载本地音乐到队列')
        self._host._description = self._description

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LOCAL, {}))


class LikedQueueButton(SpeakerControlButton):
    """一键喜欢按钮 - 清空队列并随机加载“我喜欢的音乐”最多32首。"""

    _TEXT = '一键喜欢'

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._logged_in = False
        self._provider = 'netease'
        self._description = TOOLTIPS['speaker_like_queue']
        self._host._description = self._description
        self._event_center.subscribe(EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status_change)
        self._sync_login_state()

    def _sync_login_state(self) -> None:
        self._logged_in, self._provider = _music_login_snapshot(
            fallback_provider=self._provider,
        )

    def _on_login_status_change(self, event: Event) -> None:
        logged_in, provider = _music_login_snapshot(
            fallback_logged_in=bool(event.data.get('logged_in', False)),
            fallback_provider=str(event.data.get('provider') or self._provider or 'netease'),
        )
        if logged_in == self._logged_in and provider == self._provider:
            return
        self._logged_in = logged_in
        self._provider = provider
        if self._control.visible:
            if logged_in:
                self._host.show()
                self._animate(1.0)
            else:
                self._host.hide()
                self._host.set_opacity(0.0)
        self.update()

    def fade_in(self):
        if self._control.visible:
            return
        self._control.visible = True
        if self._logged_in:
            self._host.show()
            self._animate(1.0)
        else:
            self._host.hide()
            self._host.set_opacity(0.0)

    def on_clicked(self):
        if not self._logged_in:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '请先登录音乐平台账号',
                'min': 0,
                'max': 60,
            }))
            return
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LIKED, {}))

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(
                EventType.MUSIC_LOGIN_STATUS_CHANGE, self._on_login_status_change
            )
        except Exception:
            pass
        super().cleanup()


class VolumeDownButton(SpeakerControlButton):
    """音量减按钮。"""

    _TEXT = '-'
    _STEP = -0.05

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._description = TOOLTIPS.get('speaker_volume_down', '音量减')
        self._host._description = self._description

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'delta': self._STEP}))
        _publish_volume_bubble(self._event_center)


class VolumeUpButton(SpeakerControlButton):
    """音量加按钮。"""

    _TEXT = '+'
    _STEP = 0.05

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._description = TOOLTIPS.get('speaker_volume_up', '音量加')
        self._host._description = self._description

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'delta': self._STEP}))
        _publish_volume_bubble(self._event_center)


class SpeakerControlButtons:
    """
    音响控制按钮组管理器。

    管理六个按钮：
      - 搜索优先级（80px）：左下锚点对齐搜索框的左上锚点
      - 暂停/播放（40px）：左下锚点对齐搜索优先级按钮的左上锚点
      - 下一曲（40px）   ：左锚点对齐暂停播放按钮的右锚点
      - 登录音乐（80px） ：左锚点对齐搜索优先级按钮的右锚点
      - 播放列表（80px） ：右下锚点对齐"搜索歌曲"按钮的右上锚点
      - 模式按钮（80px） ：左下锚点对齐"播放列表"按钮左上锚点

    所有按钮高度统一为 32px。音量滑条与搜索框整行等宽，贴在搜索框正上方，
    其余按钮整体上移一个滑条高度，保证互不重叠。
    """

    def __init__(self, speaker_search_dialog):
        self._dialog         = speaker_search_dialog
        self._search_priority_btn = SearchPriorityButton()
        self._search_priority_btn.set_dialog(speaker_search_dialog)
        self._play_pause_btn = PlayPauseButton()
        self._next_track_btn = NextTrackButton()
        self._music_login_btn = MusicLoginButton()
        self._playlist_btn   = PlaylistButton()
        self._playlist_btn.set_dialog(speaker_search_dialog)
        self._platform_mode_btn = PlatformModeButton()
        self._volume_slider = SpeakerVolumeSlider(_SEARCH_DIALOG_W, _VOLUME_SLIDER_HEIGHT)
        self._band_slider = SpeakerBandSlider(_BAND_SLIDER_WIDTH, _BAND_SLIDER_HEIGHT)
        self._buttons = [
            self._search_priority_btn,
            self._play_pause_btn,
            self._next_track_btn,
            self._music_login_btn,
            self._playlist_btn,
            self._platform_mode_btn,
        ]
        self._visible = False

        # 锚点位置（核心 ``Point``；不再需要 QPoint）
        self._anchor_point = None
        self._anchor_available = False

        # 订阅锚点响应事件
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅UI创建事件
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

    def _on_ui_create(self, event):
        """响应其他控件对播放/暂停按钮锚点的询问。"""
        target_ui_id = event.data.get('ui_id')
        request_anchor_id = event.data.get('anchor_id')

        if target_ui_id == 'play_pause_button':
            self._publish_anchor_response(request_anchor_id, target_ui_id)

    def _publish_anchor_response(self, anchor_id: str, ui_id: str) -> None:
        """发布播放/暂停按钮的全局锚点（与迁移前 ``publish_widget_anchor_response``
        同为整数 ``QPoint`` 语义，只是不再经过 Qt 控件）。"""
        point = _anchor_point_of(self._play_pause_btn, str(anchor_id or 'center'))
        self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': 'play_pause_button',
            'anchor_id': anchor_id,
            'anchor_point': Point(int(point.x), int(point.y)),
            'ui_id': ui_id,
        }))

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        if not self._anchor_available:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 响应搜索框的锚点更新
        if ui_id == 'all' and window_id == 'speaker_search_dialog':
            # 搜索框移动时的全局锚点更新
            if anchor_id == 'all':
                # 搜索框的新位置（左上角坐标）
                point = event.data.get('anchor_point')
                if point is None:
                    return
                try:
                    new_anchor_point = Point(float(point.x), float(point.y))
                except (AttributeError, TypeError, ValueError):
                    try:
                        new_anchor_point = Point(float(point[0]), float(point[1]))
                    except Exception:
                        return
                # 只在锚点位置改变时更新
                if self._anchor_point != new_anchor_point:
                    self._anchor_point = new_anchor_point
                    self._update_positions()

    def _focused_speaker(self):
        """当前锚定的音响实例（未锚定时为 None）。"""
        return getattr(self._dialog, 'focused_speaker', None)

    def set_focused_speaker(self, speaker) -> None:
        """锚定音响变化时同步频段滑条的读数与写回目标。"""
        self._band_slider.set_speaker(speaker)

    def _update_positions(self):
        """更新所有按钮的位置"""
        if self._anchor_point is None:
            return

        # self._anchor_point 是搜索框 top_left 锚点的全局坐标
        # 搜索框的尺寸
        dialog_width = _SEARCH_DIALOG_W
        anchor = coerce_point(self._anchor_point)
        if anchor is None:
            return
        anchor_x = int(anchor.x)
        anchor_y = int(anchor.y)

        # ── 音量滑条：与搜索框整行等宽，贴在搜索框正上方 ────────────────
        slider_y = anchor_y - _VOLUME_SLIDER_GAP - _VOLUME_SLIDER_HEIGHT
        self._volume_slider.move(anchor_x, slider_y)

        # ── 响应频段滑条：菜单右侧，上下与菜单上半部分对齐 ──────────────
        self._band_slider.set_speaker(self._focused_speaker())
        self._band_slider.apply_geometry(
            anchor_x + dialog_width + _BAND_SLIDER_GAP,
            anchor_y + _SEARCH_DIALOG_H - _BAND_SLIDER_HEIGHT,
            _BAND_SLIDER_HEIGHT,
        )

        # 其余按钮整体上移到滑条之上，避免与滑条重叠
        buttons_bottom_y = slider_y - _VOLUME_SLIDER_GAP

        # 六个按钮的落位都解算自「面板左上锚点」这一点（档位 1）：
        # 锚点矩形是零尺寸，偏移沿用各自原来的显式坐标。
        anchor_rect = Rect(anchor_x, anchor_y, 0.0, 0.0)
        screen = render_bridge.screen_rect_for_point(
            point=anchor, fallback_widget=self._volume_slider
        )

        # ── 搜索优先级按钮：左下锚点对齐搜索框左上锚点 ────────────────
        placement = render_bridge.resolve_placement(
            (_BTN_PLAYLIST_W, _BTN_HEIGHT), anchor_rect, screen,
            target_anchor_id='top_left', self_anchor_id='top_left',
            offset_y=buttons_bottom_y - _BTN_HEIGHT - anchor_y,
        )
        search_priority_x, search_priority_y = placement.x, placement.y
        self._search_priority_btn.move(search_priority_x, search_priority_y)

        # ── 暂停/播放按钮：左下锚点对齐搜索优先级按钮左上锚点 ──────────
        placement = render_bridge.resolve_placement(
            (_BTN_WIDTH, _BTN_HEIGHT), Rect(search_priority_x, search_priority_y, 0.0, 0.0), screen,
            target_anchor_id='top_left', self_anchor_id='bottom_left',
        )
        play_pause_x, play_pause_y = placement.x, placement.y
        self._play_pause_btn.move(play_pause_x, play_pause_y)

        # ── 下一曲按钮：左锚点对齐暂停播放按钮的右锚点 ──────────────
        placement = render_bridge.resolve_placement(
            (_BTN_WIDTH, _BTN_HEIGHT),
            Rect(play_pause_x + _BTN_WIDTH, play_pause_y + _BTN_HEIGHT // 2, 0.0, 0.0), screen,
            target_anchor_id='center', self_anchor_id='left',
        )
        next_track_x, next_track_y = placement.x, placement.y
        self._next_track_btn.move(next_track_x, next_track_y)

        # ── 登录音乐按钮：左锚点对齐搜索优先级按钮右锚点 ───────────────
        placement = render_bridge.resolve_placement(
            (_BTN_PLAYLIST_W, _BTN_HEIGHT),
            Rect(search_priority_x + _BTN_PLAYLIST_W, search_priority_y, 0.0, 0.0), screen,
            target_anchor_id='top_left', self_anchor_id='top_left',
        )
        login_x, login_y = placement.x, placement.y
        self._music_login_btn.move(login_x, login_y)

        # ── 播放列表按钮：右下锚点对齐"搜索歌曲"按钮右上锚点 ─────────
        # "搜索歌曲"按钮右上角 = (dialog_left + dialog_width, dialog_top)
        placement = render_bridge.resolve_placement(
            (_BTN_PLAYLIST_W, _BTN_HEIGHT), anchor_rect, screen,
            target_anchor_id='top_left', self_anchor_id='top_left',
            offset_x=dialog_width - _BTN_PLAYLIST_W,
            offset_y=buttons_bottom_y - _BTN_HEIGHT - anchor_y,
        )
        playlist_x, playlist_y = placement.x, placement.y
        self._playlist_btn.move(playlist_x, playlist_y)

        # ── 模式按钮：左下锚点对齐"播放列表"按钮左上锚点 ───────────────
        placement = render_bridge.resolve_placement(
            (_BTN_PLAYLIST_W, _BTN_HEIGHT),
            Rect(playlist_x, playlist_y, 0.0, 0.0), screen,
            target_anchor_id='top_left', self_anchor_id='bottom_left',
        )
        mode_x, mode_y = placement.x, placement.y
        self._platform_mode_btn.move(mode_x, mode_y)

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self._anchor_available = True

        # 发布UI创建请求
        create_event = Event(EventType.UI_CREATE, {
            'window_id': 'speaker_search_dialog',
            'anchor_id': 'top_left',
            'ui_id': 'play_pause_button'
        })
        self._event_center.publish(create_event)

        for btn in self._buttons:
            btn.fade_in()
        self._volume_slider.fade_in()
        self._band_slider.fade_in()
        self._update_positions()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False
        self._anchor_available = False
        for btn in self._buttons:
            btn.fade_out()
        self._volume_slider.fade_out()
        self._band_slider.fade_out()

    def cleanup(self):
        """清理资源"""
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CREATE, self._on_ui_create)
        self._volume_slider.cleanup()
        self._band_slider.cleanup()
        for btn in self._buttons:
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()


__all__ = [
    "ClearQueueButton",
    "HistoryQueueButton",
    "LikedQueueButton",
    "LocalQueueButton",
    "MusicLoginButton",
    "NextTrackButton",
    "PlatformModeButton",
    "PlayModeButton",
    "PlayPauseButton",
    "PlaylistButton",
    "SearchPriorityButton",
    "SpeakerControlButton",
    "SpeakerControlButtons",
    "VolumeDownButton",
    "VolumeUpButton",
]
