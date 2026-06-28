"""播放进度条 - 显示当前音乐播放进度

布局：
  - 左下锚点对齐播放列表的左上锚点
  - 绘制风格与主宠物UI一致：2px 黑色外框 + 2px 青色中框 + 粉色内背景
  - 固定宽度 240px：左侧 174px 为进度滑条，5px 青色分隔线，右侧 57px 显示剩余时长

交互：
  - 拖动滑块或点击滑条位置可修改当前播放进度
  - 已播放部分为青色填充，滑块为黑色竖线
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QRect, QPoint, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter, QColor, QPolygon

from config.config import COLORS, UI, FONT, UI_THEME
from config.font_config import get_digit_font, get_ui_font
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity


# ── 进度条专用配色（使用深色版本）─────────────────────────────────────
_PROGRESS_CYAN   = UI_THEME['deep_cyan']  # 已播放部分：深青色
_PROGRESS_HANDLE = UI_THEME['deep_pink']  # 滑块：深粉色


# ── 布局常量 ──────────────────────────────────────────────────────────
_WIDTH    = scale_px(240, min_abs=1)  # 固定宽度（px）
_HEIGHT   = scale_px(20, min_abs=1)   # 固定高度（px），与 playlist 行高一致
_LAYER    = scale_px(2, min_abs=1)
_BORDER   = _LAYER * 2  # 单侧边框总厚度（2px 黑 + 2px 青）
_SEP_W    = scale_px(5, min_abs=1)    # 滑条与时间区域之间的青色分隔线宽度（px）
_SEP_BK_W = scale_px(1, min_abs=1)    # 分隔线中的黑色细线宽度（px）
_TIME_W   = scale_px(57, min_abs=1)   # 时间显示区域宽度（px）
_PAD_X    = scale_px(4, min_abs=1)    # 文字水平内边距（px）
# 滑条区域宽度 = 总宽 - 两侧边框 - 分隔线 - 时间区域
_SLIDER_W = _WIDTH - _BORDER * 2 - _SEP_W - _TIME_W


class ProgressPanel(QWidget):
    """
    播放进度条（全局单例）。

    - 显示当前音乐的播放进度
    - 左下锚点对齐播放列表的左上锚点
    - 支持鼠标拖动和点击调整播放进度
    - 淡入淡出动画
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        get_topmost_manager().register(self)

        # ── 字体 ─────────────────────────────────────────────────────
        self._font = get_ui_font(FONT['ui_size'] - 1)
        self._font.setBold(True)
        self._time_font = get_digit_font(FONT['ui_size'] - 1)

        # ── 状态 ──────────────────────────────────────────────────────
        self._visible: bool       = False
        self._progress: float     = 0.0    # 播放进度 0.0 - 1.0
        self._remaining: int      = 0      # 剩余时长（秒）
        self._is_playing: bool    = False
        self._is_paused: bool     = False
        self._dragging: bool      = False  # 是否正在拖动滑块
        self._drag_progress: float = 0.0   # 拖动时的临时进度
        self._tick_counter: int = 0        # tick 计数器（用于每20tick请求进度）

        # ── 透明度动画 ───────────────────────────────────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # ── 事件订阅 ──────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status)
        self._event_center.subscribe(EventType.MUSIC_PROGRESS, self._on_music_progress)
        self._event_center.subscribe(EventType.MUSIC_SONG_END, self._on_song_end)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        self.setFixedSize(_WIDTH, _HEIGHT)

    # ==================================================================
    # 公开接口
    # ==================================================================

    def show_panel(self) -> None:
        """显示进度条。"""
        if self._visible:
            self.update()
            return
        self._visible = True
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        self.show()
        self._animate(1.0)

    def hide_panel(self) -> None:
        """隐藏进度条。"""
        if not self._visible:
            return
        self._visible = False
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)

    def set_position_below_playlist(self, playlist_rect: QRect) -> None:
        """
        设置位置：左下锚点对齐播放列表的左上锚点。
        
        即：进度条的左上角 = 播放列表的左上角
        """
        x = playlist_rect.x()
        y = playlist_rect.y() - _HEIGHT - scale_px(2, min_abs=1)  # 在播放列表上方
        x, y, _ = clamp_rect_position(
            x,
            y,
            _WIDTH,
            _HEIGHT,
            point=playlist_rect.center(),
            fallback_widget=self,
        )
        self.move(x, y)

    # ==================================================================
    # 私有：进度更新
    # ==================================================================

    def _on_music_progress(self, event: Event) -> None:
        """处理播放进度事件（由音乐管理器响应请求后发布）。"""
        if self._dragging or not self._visible:
            return
        
        # 从事件中获取进度百分比和剩余时间
        progress = event.data.get('progress', 0.0)
        remaining = event.data.get('remaining', 0)
        
        self._progress = progress
        self._remaining = remaining
        self.update()

    def _on_song_end(self, event: Event) -> None:
        """处理歌曲播放结束事件。"""
        # 重置状态
        self._progress = 0.0
        self._remaining = 0
        self.update()

    # ==================================================================
    # 私有：尺寸与位置
    # ==================================================================

    def _get_slider_rect(self) -> QRect:
        """获取滑条区域矩形（不含分隔线和时间区域）。"""
        return QRect(_BORDER, _BORDER, _SLIDER_W, _HEIGHT - _BORDER * 2)

    def _get_sep_rect(self) -> QRect:
        """获取滑条与时间区域之间的分隔线矩形。"""
        return QRect(_BORDER + _SLIDER_W, 0, _SEP_W, _HEIGHT)

    def _get_time_rect(self) -> QRect:
        """获取时间显示区域矩形。"""
        return QRect(_BORDER + _SLIDER_W + _SEP_W, _BORDER, _TIME_W, _HEIGHT - _BORDER * 2)

    def _progress_to_x(self, progress: float) -> int:
        """将进度值转换为滑条X坐标。"""
        slider_rect = self._get_slider_rect()
        return int(slider_rect.x() + progress * slider_rect.width())

    def _x_to_progress(self, x: int) -> float:
        """将滑条X坐标转换为进度值。"""
        slider_rect = self._get_slider_rect()
        # 限制在滑条范围内
        clamped_x = max(slider_rect.x(), min(x, slider_rect.x() + slider_rect.width()))
        progress = (clamped_x - slider_rect.x()) / slider_rect.width()
        return max(0.0, min(1.0, progress))

    # ==================================================================
    # 私有：动画
    # ==================================================================

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_fade_out_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        if not self._visible:
            self.hide()

    # ==================================================================
    # 鼠标事件
    # ==================================================================

    def mousePressEvent(self, event) -> None:
        """鼠标按下：开始拖动或跳转到点击位置。"""
        if event.button() != Qt.LeftButton:
            return
            
        # 点击整个进度条区域都可以调整进度
        self._dragging = True
        self._drag_progress = self._x_to_progress(event.x())
        self.update()

    def mouseMoveEvent(self, event) -> None:
        """鼠标移动：拖动滑块。"""
        if self._dragging:
            self._drag_progress = self._x_to_progress(event.x())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        """鼠标释放：发布进度百分比事件。"""
        if event.button() != Qt.LeftButton:
            return
            
        if self._dragging:
            self._dragging = False
            self._progress = self._drag_progress
            
            # 发布进度百分比事件，由音乐模块计算实际位置
            self._event_center.publish(Event(EventType.MUSIC_SEEK, {
                'progress': self._progress,
            }))
            self.update()

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_frame(self, event: Event) -> None:
        """帧事件处理：仅负责位置跟随播放列表。"""
        if not self._visible:
            return
        # 跟随播放列表位置
        from lib.script.ui.playlist_panel import get_playlist_panel
        playlist_panel = get_playlist_panel()
        if playlist_panel and playlist_panel.is_visible:
            self.set_position_below_playlist(playlist_panel.geometry())
        else:
            # 播放列表不可见时隐藏进度条
            self.hide_panel()
            return

    def _on_tick(self, event: Event) -> None:
        """Tick 事件处理：固定节奏请求音乐进度。"""
        if not self._visible or self._dragging:
            return
        self._tick_counter += 1
        if self._tick_counter < 20:
            return
        self._tick_counter = 0
        self._event_center.publish(Event(EventType.MUSIC_PROGRESS_REQUEST, {}))

    def _on_music_status(self, event: Event) -> None:
        """播放状态变化时更新。"""
        self._is_playing = event.data.get('playing', False)
        self._is_paused = event.data.get('paused', False)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._font)

        # ── 内层背景（先绘制，外黑边框最后覆盖）────────────────────────
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), COLORS['cyan'])
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), COLORS['pink'])

        progress = self._drag_progress if self._dragging else self._progress

        # ── 进度滑条区域 ───────────────────────────────────────────
        slider_rect = self._get_slider_rect()

        # 已播放部分（高饱和度青色填充）
        fill_width = int(progress * slider_rect.width())
        if fill_width > 0:
            painter.fillRect(
                QRect(slider_rect.x(), slider_rect.y(), fill_width, slider_rect.height()),
                _PROGRESS_CYAN
            )

        # 滑块：深粉色菱形（接近正方形）
        handle_x = slider_rect.x() + fill_width
        handle_x = max(slider_rect.x(), min(slider_rect.right(), handle_x))
        cx = handle_x
        cy = slider_rect.y() + slider_rect.height() // 2
        half_size = max(1, slider_rect.height() // 2 - scale_px(1, min_abs=1))  # 统一尺寸，形成接近正方形的菱形
        diamond = QPolygon([
            QPoint(cx,             cy - half_size),
            QPoint(cx + half_size, cy),
            QPoint(cx,             cy + half_size),
            QPoint(cx - half_size, cy),
        ])
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(_PROGRESS_HANDLE)
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(diamond)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # ── 分隔线（仅在内容区域内绘制，不超出边框）──────────────────
        sep_rect = self._get_sep_rect()
        painter.fillRect(sep_rect, COLORS['cyan'])
        black_x = sep_rect.x() + (sep_rect.width() - _SEP_BK_W) // 2
        inner_y = _BORDER
        inner_h = _HEIGHT - _BORDER * 2
        painter.fillRect(QRect(black_x, inner_y, _SEP_BK_W, inner_h), COLORS['black'])

        # ── 剩余时间显示（拉海洛字体）─────────────────────────────────
        time_rect = self._get_time_rect()
        if self._dragging:
            # 拖动时：根据当前进度反推剩余时间
            # 假设 remaining 对应的是 (1 - _progress) 的比例
            # 那么 total = remaining / (1 - _progress)，拖动后剩余 = total * (1 - _drag_progress)
            if self._progress > 0 and self._progress < 1.0:
                total_time = self._remaining / (1 - self._progress)
                remaining = int(total_time * (1 - self._drag_progress))
            else:
                remaining = self._remaining
        else:
            remaining = self._remaining
        minutes = remaining // 60
        seconds = remaining % 60
        time_text = f"{minutes}:{seconds:02d}"

        painter.setFont(self._time_font)
        painter.setPen(COLORS['text'])
        painter.drawText(time_rect, Qt.AlignCenter, time_text)
        painter.setFont(self._font)

        # ── 外黑边框最后绘制，覆盖所有内容 ───────────────────────────
        r = self.rect()
        top_h = _LAYER
        side_w = _LAYER
        painter.fillRect(QRect(r.x(),          r.y(),              r.width(), top_h),  COLORS['black'])
        painter.fillRect(QRect(r.x(),          r.bottom() - top_h + 1, r.width(), top_h),  COLORS['black'])
        painter.fillRect(QRect(r.x(),          r.y(),              side_w,    r.height()), COLORS['black'])
        painter.fillRect(QRect(r.right() - side_w + 1, r.y(),      side_w,    r.height()), COLORS['black'])

        painter.end()

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


# ── 全局单例 ──────────────────────────────────────────────────────────
_instance: 'ProgressPanel | None' = None


def get_progress_panel() -> 'ProgressPanel | None':
    """获取全局进度条单例（未初始化时返回 None）。"""
    return _instance


def init_progress_panel() -> 'ProgressPanel':
    """初始化并返回全局进度条单例，需在 Qt 主线程中调用。"""
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
