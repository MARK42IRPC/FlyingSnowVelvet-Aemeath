"""音响控制按钮类 - 暂停/播放、下一曲"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QPointF, QRectF
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF

from config.config import COLORS, FONT, UI_THEME, SPEAKER_SEARCH_UI
from config.font_config import get_ui_font
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.script.music import get_music_service
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
    animate_opacity,
)


# ── 配色（从 UI_THEME 获取）──────────────────────────────────────────
_C_BORDER = UI_THEME['border']
_C_MID    = UI_THEME['mid']
_C_BG     = UI_THEME['bg']
_C_TEXT   = UI_THEME['text']
_C_ICON   = UI_THEME['icon']

# ── 尺寸 ──────────────────────────────────────────────────────────────
_BTN_WIDTH        = scale_px(40, min_abs=1)  # 图标按钮宽度（播放/暂停、下一曲）
_BTN_HEIGHT       = scale_px(32, min_abs=1)  # 所有按钮统一高度
_BTN_PLAYLIST_W   = scale_px(80, min_abs=1)  # 播放列表按钮宽度（与搜索按钮等宽）
_LAYER            = scale_px(2, min_abs=1)
_BORDER           = _LAYER * 2
_SEARCH_DIALOG_W  = SPEAKER_SEARCH_UI.get('input_width', scale_px(160, min_abs=1)) + SPEAKER_SEARCH_UI.get('button_width', scale_px(80, min_abs=1))


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


class SpeakerControlButton(QWidget):
    """
    音响控制按钮基类。

    按钮样式：
      - 2px 黑色外框
      - 2px 灰白色中框
      - 棕色背景
      - 白色几何图标
    """

    def __init__(self, width: int = _BTN_WIDTH, height: int = _BTN_HEIGHT):
        super().__init__()
        self._width = width
        self._height = height

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        get_topmost_manager().register(self)

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._visible = False
        self._pressed = False
        self._description = ''   # 由各子类覆盖

        # 事件中心
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def get_anchor_point(self, anchor_id: str) -> QPoint:
        """获取指定锚点的位置"""
        return resolve_anchor_point(self, anchor_id)

    def paintEvent(self, event):
        """绘制按钮"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 绘制2px黑色边框（最外层）
        painter.fillRect(self.rect(), _C_BORDER)

        # 绘制2px灰白色边框（中间层）
        mid_rect = self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
        painter.fillRect(mid_rect, _C_MID)

        # 绘制棕色背景（最内层）
        content_rect = self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER)
        painter.fillRect(content_rect, _C_BG)

        # 绘制几何图标（由子类实现）
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_icon(painter, content_rect)

    def _draw_icon(self, painter, rect):
        """子类重写此方法绘制几何图标"""
        pass

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

    def _on_fade_out_complete(self):
        self._anim.finished.disconnect(self._on_fade_out_complete)
        self.hide()

    def _animate(self, target: float):
        animate_opacity(self._anim, self._opacity, target)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.on_clicked()
            self.update()

    def on_clicked(self):
        """子类重写此方法实现点击逻辑"""
        pass


class PlayPauseButton(SpeakerControlButton):
    """暂停/播放按钮 - 使用几何形状绘制"""

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._playing     = False
        self._description = TOOLTIPS['speaker_play_pause']

        # 订阅播放状态变化事件
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)
        
        # 初始化时主动获取当前播放状态
        self._sync_playing_state()

    def _sync_playing_state(self):
        """??????????????"""
        self.set_playing(_music_is_playing())

    def set_playing(self, playing: bool):
        """设置播放状态，更新图标"""
        if self._playing != playing:
            self._playing = playing
            self.update()

    def _on_status_change(self, event: Event):
        """处理播放状态变化事件"""
        playing = event.data.get('playing', False)
        self.set_playing(playing)

    def _draw_icon(self, painter, rect):
        """绘制播放/暂停几何图标"""
        cx = rect.center().x()
        cy = rect.center().y()
        size = min(rect.width(), rect.height()) * 0.4 * 0.85  # 缩小到 0.85

        painter.setPen(Qt.NoPen)
        painter.setBrush(_C_ICON)

        if self._playing:
            # 暂停图标：两条竖线
            bar_width = size * 0.3
            bar_height = size * 1.4
            gap = size * 0.4

            painter.drawRect(QRectF(cx - gap - bar_width, cy - bar_height // 2, bar_width, bar_height))
            painter.drawRect(QRectF(cx + gap, cy - bar_height // 2, bar_width, bar_height))
        else:
            # 播放图标：三角形
            triangle = QPolygonF([
                QPointF(cx - size * 0.3, cy - size * 0.6),
                QPointF(cx - size * 0.3, cy + size * 0.6),
                QPointF(cx + size * 0.5, cy)
            ])
            painter.drawPolygon(triangle)

    def on_clicked(self):
        """切换播放/暂停状态"""
        # 发布播放控制事件，不本地更新状态，由事件中心广播回来更新
        self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {
            'playing': not self._playing
        }))


class NextTrackButton(SpeakerControlButton):
    """下一曲按钮 - 使用几何形状绘制"""

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._description = TOOLTIPS['speaker_next']

    def _draw_icon(self, painter, rect):
        """绘制下一曲几何图标"""
        cx = rect.center().x()
        cy = rect.center().y()
        size = min(rect.width(), rect.height()) * 0.4

        painter.setPen(Qt.NoPen)
        painter.setBrush(_C_ICON)

        # 三角形（播放图标）
        triangle = QPolygonF([
            QPointF(cx - size * 0.4, cy - size * 0.5),
            QPointF(cx - size * 0.4, cy + size * 0.5),
            QPointF(cx + size * 0.2, cy)
        ])
        painter.drawPolygon(triangle)

        # 竖线（下一曲图标）
        bar_width = size * 0.25
        bar_height = size * 1.2
        painter.drawRect(QRectF(cx + size * 0.3, cy - bar_height // 2, bar_width, bar_height))

    def on_clicked(self):
        """播放下一曲"""
        # 发布下一曲事件
        self._event_center.publish(Event(EventType.MUSIC_NEXT_TRACK, {}))


class MusicLoginButton(SpeakerControlButton):
    """音乐平台登录按钮（80px 文字按钮）。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._logged_in = False
        self._provider = 'netease'
        self._description = TOOLTIPS['speaker_music_login']
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

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        try:
            provider = self._provider
        except Exception:
            provider = 'netease'
        if self._logged_in:
            label = '已登录'
        elif provider == 'qq':
            label = '登录QQ'
        elif provider == 'kugou':
            label = '登录酷狗'
        else:
            label = '登录音乐'
        painter.drawText(rect, Qt.AlignCenter, label)

    def on_clicked(self):
        if self._logged_in:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '音乐平台账号已登录',
                'min': 0,
                'max': 60,
            }))
            return
        self._event_center.publish(Event(EventType.MUSIC_LOGIN_REQUEST, {}))


class PlatformModeButton(SpeakerControlButton):
    """Platform music mode switch button."""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._mode_label = "网易模式"
        self._description = TOOLTIPS.get('speaker_platform_mode', '切换当前音乐平台模式')
        self._sync_mode_state()

    def _sync_mode_state(self) -> None:
        self._mode_label = _music_provider_mode_label()

    def fade_in(self):
        self._sync_mode_state()
        super().fade_in()

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, self._mode_label)

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
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._mode = 'list_loop'
        self._description = TOOLTIPS['speaker_play_mode']
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_status_change)
        self._sync_mode_state()

    def _sync_mode_state(self) -> None:
        self._mode = _music_play_mode()

    def _on_status_change(self, event: Event) -> None:
        mode = str(event.data.get('play_mode', self._mode))
        if mode != self._mode:
            self._mode = mode
            self.update()

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        label = self._MODE_LABELS.get(self._mode, self._MODE_LABELS['list_loop'])
        painter.drawText(rect, Qt.AlignCenter, label)

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_PLAY_MODE_TOGGLE, {}))


class SearchPriorityButton(SpeakerControlButton):
    """搜索优先级按钮 - 单曲/歌手/专辑/歌单优先切换。"""

    _FALLBACK_LABELS = ('单曲优先', '歌手优先', '专辑优先', '歌单优先')

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._dialog = None
        self._label_index = 0
        self._label = self._FALLBACK_LABELS[self._label_index]
        self._description = TOOLTIPS.get('speaker_search_priority', '切换搜索优先级')

    def set_dialog(self, dialog) -> None:
        self._dialog = dialog
        try:
            self._label = str(dialog.search_priority_label)
        except Exception:
            self._label = self._FALLBACK_LABELS[self._label_index]
        self.update()

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, self._label)

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

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font  = get_ui_font()
        self._label_font.setBold(True)
        self._dialog      = None   # 由 SpeakerControlButtons 注入
        self._description = TOOLTIPS['speaker_playlist_toggle']

    def set_dialog(self, dialog) -> None:
        """注入搜索对话框引用，用于获取当前锚定音响。"""
        self._dialog = dialog

    def _draw_icon(self, painter, rect):
        """绘制文字标签（覆盖基类的图标绘制方法）。"""
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '播放列表')

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
            from lib.script.ui.progress_panel import get_progress_panel, init_progress_panel
            # 初始化进度条（懒初始化单例）
            progress_panel = get_progress_panel() or init_progress_panel()
            # 初始化播放列表
            panel = get_playlist_panel() or init_playlist_panel()
            panel.show_for(speaker)


class HistoryQueueButton(SpeakerControlButton):
    """一键历史按钮 - 将 history.json 中歌曲批量追加到播放队列末尾。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._description = TOOLTIPS['speaker_history_queue']

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '一键历史')

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_HISTORY, {}))


class ClearQueueButton(SpeakerControlButton):
    """清空列表按钮 - 停止播放并清空当前队列。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._description = TOOLTIPS.get('speaker_clear_queue', '清空列表')

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '清空列表')

    def on_clicked(self):
        get_music_service().clear_queue()


class LocalQueueButton(SpeakerControlButton):
    """一键本地按钮 - 清空队列并载入本地音乐文件夹中的全部歌曲。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._description = TOOLTIPS.get('speaker_local_queue', '加载本地音乐到队列')

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '一键本地')

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LOCAL, {}))


class LikedQueueButton(SpeakerControlButton):
    """一键喜欢按钮 - 清空队列并随机加载“我喜欢的音乐”最多32首。"""

    def __init__(self):
        super().__init__(_BTN_PLAYLIST_W, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._logged_in = False
        self._provider = 'netease'
        self._description = TOOLTIPS['speaker_like_queue']
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
        if self._visible:
            if logged_in:
                self.show()
                self._animate(1.0)
            else:
                self.hide()
                self._opacity.setOpacity(0.0)
        self.update()

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '一键喜欢')

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        if self._logged_in:
            self.show()
            self._animate(1.0)
        else:
            self.hide()
            self._opacity.setOpacity(0.0)

    def on_clicked(self):
        if not self._logged_in:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '请先登录音乐平台账号',
                'min': 0,
                'max': 60,
            }))
            return
        self._event_center.publish(Event(EventType.MUSIC_ENQUEUE_LIKED, {}))


class VolumeDownButton(SpeakerControlButton):
    """??????"""

    _STEP = -0.05

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._description = TOOLTIPS.get('speaker_volume_down', '????')

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '-')

    def on_clicked(self):
        self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'delta': self._STEP}))
        _publish_volume_bubble(self._event_center)


class VolumeUpButton(SpeakerControlButton):
    """??????"""

    _STEP = 0.05

    def __init__(self):
        super().__init__(_BTN_WIDTH, _BTN_HEIGHT)
        self._label_font = get_ui_font()
        self._label_font.setBold(True)
        self._description = TOOLTIPS.get('speaker_volume_up', '????')

    def _draw_icon(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._label_font)
        painter.setPen(_C_TEXT)
        painter.drawText(rect, Qt.AlignCenter, '+')

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

    所有按钮高度统一为 32px。
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
        self._buttons = [
            self._search_priority_btn,
            self._play_pause_btn,
            self._next_track_btn,
            self._music_login_btn,
            self._playlist_btn,
            self._platform_mode_btn,
        ]
        self._visible = False

        # 锚点位置
        self._anchor_point = None
        self._anchor_available = False

        # 订阅锚点响应事件
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅UI创建事件
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

    def _on_ui_create(self, event):
        """UI?????? - ???????"""
        target_ui_id = event.data.get('ui_id')
        request_anchor_id = event.data.get('anchor_id')

        if target_ui_id == 'play_pause_button':
            publish_widget_anchor_response(
                self._event_center,
                self._play_pause_btn,
                window_id='play_pause_button',
                anchor_id=request_anchor_id,
                ui_id=target_ui_id,
            )

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
                dialog_pos = event.data.get('anchor_point')
                # 计算 top_left 锚点位置
                new_anchor_point = QPoint(dialog_pos.x(), dialog_pos.y())
                # 只在锚点位置改变时更新
                if self._anchor_point != new_anchor_point:
                    self._anchor_point = new_anchor_point
                    self._update_positions()

    def _update_positions(self):
        """更新所有按钮的位置"""
        if not self._anchor_point:
            return

        # self._anchor_point 是搜索框 top_left 锚点的全局坐标
        # 搜索框的尺寸
        dialog_width = _SEARCH_DIALOG_W

        # ── 搜索优先级按钮：左下锚点对齐搜索框左上锚点 ────────────────
        search_priority_x, search_priority_y, _ = clamp_rect_position(
            self._anchor_point.x(),
            self._anchor_point.y() - _BTN_HEIGHT - scale_px(2, min_abs=1),
            _BTN_PLAYLIST_W,
            _BTN_HEIGHT,
            point=self._anchor_point,
            fallback_widget=self._search_priority_btn,
        )
        self._search_priority_btn.move(search_priority_x, search_priority_y)

        # ── 暂停/播放按钮：左下锚点对齐搜索优先级按钮左上锚点 ──────────
        play_pause_x, play_pause_y, _ = clamp_rect_position(
            search_priority_x,
            search_priority_y - _BTN_HEIGHT,
            _BTN_WIDTH,
            _BTN_HEIGHT,
            point=self._anchor_point,
            fallback_widget=self._play_pause_btn,
        )
        self._play_pause_btn.move(play_pause_x, play_pause_y)

        # ── 下一曲按钮：左锚点对齐暂停播放按钮的右锚点 ──────────────
        next_track_x, next_track_y, _ = clamp_rect_position(
            play_pause_x + _BTN_WIDTH,
            play_pause_y,
            _BTN_WIDTH,
            _BTN_HEIGHT,
            point=self._anchor_point,
            fallback_widget=self._next_track_btn,
        )
        self._next_track_btn.move(next_track_x, next_track_y)

        # ── 登录音乐按钮：左锚点对齐搜索优先级按钮右锚点 ───────────────
        login_x, login_y, _ = clamp_rect_position(
            search_priority_x + _BTN_PLAYLIST_W,
            search_priority_y,
            _BTN_PLAYLIST_W,
            _BTN_HEIGHT,
            point=self._anchor_point,
            fallback_widget=self._music_login_btn,
        )
        self._music_login_btn.move(login_x, login_y)

        # ── 播放列表按钮：右下锚点对齐"搜索歌曲"按钮右上锚点 ─────────
        # "搜索歌曲"按钮右上角 = (dialog_left + dialog_width, dialog_top)
        playlist_x, playlist_y, _ = clamp_rect_position(
            self._anchor_point.x() + dialog_width - _BTN_PLAYLIST_W,
            self._anchor_point.y() - _BTN_HEIGHT - scale_px(2, min_abs=1),
            _BTN_PLAYLIST_W,
            _BTN_HEIGHT,
            point=self._anchor_point,
            fallback_widget=self._playlist_btn,
        )
        self._playlist_btn.move(playlist_x, playlist_y)

        # ── 模式按钮：左下锚点对齐"播放列表"按钮左上锚点 ───────────────
        mode_x, mode_y, _ = clamp_rect_position(
            playlist_x,
            playlist_y - _BTN_HEIGHT,
            _BTN_PLAYLIST_W,
            _BTN_HEIGHT,
            point=QPoint(playlist_x, playlist_y),
            fallback_widget=self._platform_mode_btn,
        )
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
        self._update_positions()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False
        self._anchor_available = False
        for btn in self._buttons:
            btn.fade_out()

    def cleanup(self):
        """清理资源"""
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CREATE, self._on_ui_create)
        for btn in self._buttons:
            try:
                btn.close()
            except Exception:
                pass
        self._buttons.clear()
