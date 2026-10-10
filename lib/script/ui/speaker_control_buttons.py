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

from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.core.render.visuals.types import Point, Rect, coerce_point
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

#: 按钮基类与无 Qt 助手已下沉到 `speaker_button_shell.py`；这里按原名重新导出，
#: 既有导入面不变（`playlist_panel.py` 取 `_BTN_*` 常量、包级懒导出取 `SpeakerControlButton`）。
from lib.script.ui.speaker_button_shell import (  # noqa: F401
    _BAND_SLIDER_GAP,
    _BTN_HEIGHT,
    _BTN_PLAYLIST_W,
    _BTN_WIDTH,
    _SEARCH_DIALOG_H,
    _SEARCH_DIALOG_W,
    _VOLUME_SLIDER_GAP,
    SpeakerControlButton,
    _anchor_point_of,
    _music_is_playing,
    _music_login_snapshot,
    _music_play_mode,
    _music_provider_mode_label,
    _publish_volume_bubble,
)


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
