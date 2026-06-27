"""播放列表栏 - 显示当前播放队列

布局：
  - 左锚点对齐当前锚定音响的右锚点（右边缘 + 6px 间距），垂直居中于音响
  - 绘制风格与命令提示框一致：2px 黑色外框 + 2px 青色中框 + 粉色内背景
  - 固定宽度 240xp，高度随队列条目数自适应

显示内容：
  - 队列中的所有歌曲，每行一首
  - 当前正在播放的歌曲：青色行背景 + "♪ " 前缀高亮
  - 队列为空时显示"（队列为空）"

单例：全局唯一实例，随音响锚定自动跟随移动。
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QRect, QPointF, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter, QPen, QPolygonF, QCursor

from config.config import COLORS, UI, UI_THEME
from config.font_config import get_ui_font, get_digit_font, draw_mixed_text, elide_mixed_text
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.music import get_music_service
from lib.script.ui.speaker_control_buttons import (
    PlayPauseButton,
    NextTrackButton,
    HistoryQueueButton,
    ClearQueueButton,
    LocalQueueButton,
    LikedQueueButton,
    VolumeUpButton,
    VolumeDownButton,
    PlayModeButton,
    _BTN_WIDTH,
    _BTN_HEIGHT,
    _BTN_PLAYLIST_W,
)
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position
from lib.script.ui.speaker_menu_style import (
    SpeakerActionButtonMixin,
    _C_ACTION_TEXT,
)


# ── 布局常量 ──────────────────────────────────────────────────────────
_WIDTH     = scale_px(240, min_abs=1)  # 固定宽度（xp），与 UI 说明一致
_ROW_H     = scale_px(20, min_abs=1)   # 每行高度（px），与命令提示框保持一致
_LAYER     = scale_px(2, min_abs=1)
_BORDER    = _LAYER * 2  # 单侧边框总厚度（2px 黑 + 2px 青）
_PAD_X     = scale_px(6, min_abs=1)    # 文字水平内边距（px）
_GAP       = scale_px(6, min_abs=1)    # 与音响右边缘的水平间距（px）
_PAGE_SIZE = 7     # 每页最多条目数
_C_HL      = UI_THEME['highlight']  # 选中高亮（与搜索列表一致）
_REMOVE_BTN_W = scale_px(20, min_abs=1)
_REMOVE_BTN_H = scale_px(20, min_abs=1)
_AUTO_HIDE_MOUSE_DISTANCE = UI.get('auto_hide_mouse_distance', scale_px(300, min_abs=1))


class _QueueRemoveButton(SpeakerActionButtonMixin, QWidget):
    """队列删除按钮（x）。"""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._pressed = False
        self._visible = False
        self._description = ''
        self._init_speaker_action_button(_REMOVE_BTN_W, _REMOVE_BTN_H)
        self.setFocusPolicy(Qt.NoFocus)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        ec = get_event_center()
        ec.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def show_btn(self) -> None:
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)

    def hide_btn(self) -> None:
        self._cancel_action_press()
        if not self._visible:
            return
        self._visible = False
        self._anim.finished.connect(self._on_fade_done)
        self._animate(0.0)

    def _on_fade_done(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_done)
        except (RuntimeError, TypeError):
            pass
        if not self._visible:
            self.hide()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self._begin_action_press()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            commit = self.rect().contains(event.pos())
            if self._finish_action_press(commit=commit):
                if self._callback:
                    self._callback()
        event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        content = self._paint_action_button_shell(p)

        p.setRenderHint(QPainter.Antialiasing, True)
        icon = content.adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER)
        pen = QPen(_C_ACTION_TEXT)
        pen.setWidth(_LAYER)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(icon.topLeft(), icon.bottomRight())
        p.drawLine(icon.topRight(), icon.bottomLeft())
        p.end()


class _QueuePlayNowButton(_QueueRemoveButton):
    """队列立即播放按钮（▶）。"""

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        content = self._paint_action_button_shell(p)

        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(_C_ACTION_TEXT)
        cx = content.center().x()
        cy = content.center().y()
        half_w = max(3, content.width() // 4)
        half_h = max(3, content.height() // 4)
        p.drawPolygon(QPolygonF([
            QPointF(cx - half_w, cy - half_h),
            QPointF(cx - half_w, cy + half_h),
            QPointF(cx + half_w, cy),
        ]))
        p.end()


class PlaylistPanel(QWidget):
    """
    播放列表栏（全局单例）。

    - 显示 CloudMusicManager 队列中的所有歌曲
    - 当前播放行：青色背景高亮 + "♪ " 前缀
    - 使用事件驱动更新，避免每帧轮询
    - 跟随锚定音响的右侧位置移动；音响消失时自动关闭
    - 淡入淡出动画 + right_fade 粒子消散特效
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        get_topmost_manager().register(self)

        # ── 字体（粗体，与命令提示框一致）──────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()

        # ── 状态 ──────────────────────────────────────────────────────
        self._visible: bool      = False
        self._description        = TOOLTIPS['playlist_panel']
        self._focused_speaker    = None       # 当前锚定的 Speaker 实例
        self._queue: list        = []         # [(song_id, display), ...]
        self._current_index: int = -1         # 正在播放的行索引
        self._page: int          = 0          # 当前页码（0-based）
        self._selected: int      = -1         # 当前页高亮行（0-based）

        # ── 翻页按钮 ──────────────────────────────────────────────────
        self._prev_btn, self._next_btn = make_page_buttons(
            lambda: self._turn_page(-1),
            lambda: self._turn_page(1),
        )
        self._remove_btn = _QueueRemoveButton(self._remove_selected_song)
        self._remove_btn._description = TOOLTIPS['playlist_remove_song']
        self._play_now_btn = _QueuePlayNowButton(self._play_selected_song)
        self._play_now_btn._description = TOOLTIPS['playlist_play_now']

        # ── 透明度动画 ───────────────────────────────────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # ── 事件订阅 ──────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME,                  self._on_frame)
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE,    self._on_music_status)
        self._event_center.subscribe(EventType.MUSIC_SONG_END,         self._on_song_end)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # ── 控制按钮（暂停/播放 + 下一曲 + 音量加/减 + 播放模式）────────
        self._play_pause_btn  = PlayPauseButton()
        self._next_track_btn  = NextTrackButton()
        self._history_queue_btn = HistoryQueueButton()
        self._clear_queue_btn = ClearQueueButton()
        self._local_queue_btn = LocalQueueButton()
        self._liked_queue_btn = LikedQueueButton()
        self._volume_up_btn   = VolumeUpButton()
        self._volume_down_btn = VolumeDownButton()
        self._play_mode_btn   = PlayModeButton()

        self._refresh_size()

    # ==================================================================
    # 公开接口
    # ==================================================================

    def _iter_control_buttons(self):
        return [
            self._play_pause_btn,
            self._next_track_btn,
            self._history_queue_btn,
            self._clear_queue_btn,
            self._local_queue_btn,
            self._liked_queue_btn,
            self._volume_up_btn,
            self._volume_down_btn,
            self._play_mode_btn,
        ]

    def _set_control_buttons_visible(self, visible: bool) -> None:
        for button in self._iter_control_buttons():
            if visible:
                button.fade_in()
            else:
                button.fade_out()

    def show_for(self, speaker) -> None:
        """打开面板并锚定到指定音响。若已对同一音响显示则仅刷新。"""
        self._focused_speaker = speaker
        self._refresh_content()
        self._refresh_size()
        self._update_position()
        if self._visible:
            self.update()
            self._update_progress_panel_position()
            self._update_remove_button_position()
            self.setFocus(Qt.ActiveWindowFocusReason)
            return
        self._visible = True
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        self.show()
        self.setFocus(Qt.ActiveWindowFocusReason)
        self._animate(1.0)
        # 显示进度条
        self._show_progress_panel()
        # 显示控制按钮
        self._set_control_buttons_visible(True)
        self._update_control_buttons_position()
        self._update_remove_button_position()

    def hide_panel(self) -> None:
        """淡出隐藏，发射 right_fade 粒子消散特效。"""
        if not self._visible:
            return
        self._visible        = False
        self._focused_speaker = None
        rect = self.geometry()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (rect.x(), rect.y(),
                            rect.x() + rect.width(), rect.y() + rect.height()),
        }))
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)
        # 隐藏进度条
        self._hide_progress_panel()
        # 隐藏控制按钮
        self._set_control_buttons_visible(False)
        self._prev_btn.hide_btn()
        self._next_btn.hide_btn()
        self._remove_btn.hide_btn()
        self._play_now_btn.hide_btn()

    @property
    def is_visible(self) -> bool:
        """返回面板是否可见。"""
        return self._visible

    # ==================================================================
    # 鼠标事件
    # ==================================================================

    def mouseMoveEvent(self, event) -> None:
        row = self._row_from_pos(event.pos().y())
        if row != -1 and row != self._selected:
            self._selected = row
            self._update_remove_button_position()
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        self.setFocus(Qt.MouseFocusReason)
        row = self._row_from_pos(event.pos().y())
        if row != -1 and row != self._selected:
            self._selected = row
            self._update_remove_button_position()
            self.update()
        if row != -1:
            if event.button() == Qt.LeftButton:
                self.move_selected_by_key(-1)
                return
            if event.button() == Qt.RightButton:
                self.move_selected_by_key(1)
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Left:
            self.move_selected_by_key(-1)
            event.accept()
            return
        if key == Qt.Key_Right:
            self.move_selected_by_key(1)
            event.accept()
            return
        super().keyPressEvent(event)

    def move_selected_by_key(self, direction: int) -> bool:
        """
        供键盘路由调用：移动当前高亮歌曲。

        Args:
            direction: -1 上移，+1 下移。

        Returns:
            True 表示已处理该按键（包含被规则拦截的情况）。
        """
        if direction not in (-1, 1):
            return False
        if not self._visible:
            return False
        self._move_selected(direction)
        return True

    # ==================================================================
    # 私有：数据刷新
    # ==================================================================

    def _refresh_content(self, reset_page: bool = True, preferred_index: int | None = None) -> None:
        """从 CloudMusicManager 读取最新队列状态并同步页码/选中项。"""
        old_selected_abs = self._selected_abs_index()
        service = get_music_service()
        self._queue = service.queue_snapshot()
        self._current_index = service.current_index()

        if not self._queue:
            self._page = 0
            self._selected = -1
            return

        max_page = (len(self._queue) - 1) // _PAGE_SIZE

        if preferred_index is not None:
            preferred_index = max(0, min(preferred_index, len(self._queue) - 1))
            self._page = preferred_index // _PAGE_SIZE
            self._selected = preferred_index % _PAGE_SIZE
            return

        if reset_page:
            self._page = 0
            self._selected = self._default_selected_row()
            return

        self._page = max(0, min(self._page, max_page))
        if old_selected_abs >= 0:
            old_selected_abs = min(old_selected_abs, len(self._queue) - 1)
            self._page = old_selected_abs // _PAGE_SIZE
            self._selected = old_selected_abs % _PAGE_SIZE
        else:
            self._selected = self._default_selected_row()

    # ==================================================================
    # 私有：尺寸与位置
    # ==================================================================

    def _refresh_size(self) -> None:
        """??????????????? 1 ??????? _WIDTH?"""
        items  = self._page_items()
        n_rows = max(1, len(items))
        if self._has_pages():
            n_rows += 1   # ??????
        self.setFixedSize(_WIDTH, _BORDER * 2 + n_rows * _ROW_H)

    def _refresh_view_state(
        self,
        *,
        refresh_content: bool = False,
        reset_page: bool = True,
        preferred_index: int | None = None,
        reposition: bool = False,
        update_remove_button: bool = False,
    ) -> None:
        if refresh_content:
            self._refresh_content(reset_page=reset_page, preferred_index=preferred_index)
        self._refresh_size()
        if reposition:
            self._update_position()
        elif update_remove_button:
            self._update_remove_button_position()
        self.update()

    def _page_items(self) -> list:
        if not self._queue:
            return []
        start = self._page * _PAGE_SIZE
        return self._queue[start: start + _PAGE_SIZE]

    def _has_pages(self) -> bool:
        return len(self._queue) > _PAGE_SIZE

    def _selected_abs_index(self) -> int:
        """返回当前高亮行在全队列中的绝对索引。"""
        if self._selected < 0:
            return -1
        idx = self._page * _PAGE_SIZE + self._selected
        if 0 <= idx < len(self._queue):
            return idx
        return -1

    def _default_selected_row(self) -> int:
        """
        默认选中当前页的第一首非正在播放歌曲；
        若本页只有当前播放项，则回退到第 0 行。
        """
        items = self._page_items()
        if not items:
            return -1
        page_offset = self._page * _PAGE_SIZE
        for i in range(len(items)):
            if page_offset + i != self._current_index:
                return i
        return 0

    def _row_from_pos(self, y_pos: int) -> int:
        """根据鼠标 y 坐标计算当前页行号（不在歌曲行返回 -1）。"""
        if not self._queue:
            return -1
        y_in = y_pos - _BORDER
        if y_in < 0:
            return -1
        row = y_in // _ROW_H
        items = self._page_items()
        if 0 <= row < len(items):
            return row
        return -1

    def _move_selected(self, direction: int) -> None:
        """?????????????-1 ?? / +1 ????"""
        if not self._queue:
            return

        src = self._selected_abs_index()
        if src < 0:
            self._selected = self._default_selected_row()
            self.update()
            return

        dst = src + direction
        if not (0 <= dst < len(self._queue)):
            return

        # ?????????????????????????
        if src == self._current_index or dst == self._current_index:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '??????????????',
                'min': 0,
                'max': 60,
            }))
            return

        new_index = get_music_service().move_queue_item(src, direction)
        if new_index < 0:
            return

        self._refresh_view_state(
            refresh_content=True,
            reset_page=False,
            preferred_index=new_index,
            reposition=True,
        )

    def _turn_page(self, direction: int) -> None:
        if not self._has_pages():
            return
        max_page = (len(self._queue) - 1) // _PAGE_SIZE
        new_page = self._page + direction
        if new_page < 0:
            new_page = max_page
        elif new_page > max_page:
            new_page = 0
        self._page = new_page
        self._selected = self._default_selected_row()
        self._refresh_view_state(reposition=True)

    def _update_position(self) -> None:
        """始终优先放在音响右侧；右侧受阻时翻到左侧。"""
        if not self._focused_speaker:
            return
        s        = self._focused_speaker
        anchor_y = s.y() + s.height() // 2 - self.height() // 2
        right_x = s.x() + s.width() + _GAP
        left_x = s.x() - _WIDTH - _GAP
        reserve_w = _WIDTH + _REMOVE_BTN_W * 2

        # 先尝试右侧
        x, y, _ = clamp_rect_position(
            right_x,
            anchor_y,
            reserve_w,
            self.height(),
            point=s.geometry().center(),
            fallback_widget=self,
        )

        # 右侧受阻时翻到左侧
        if x != right_x:
            fx, fy, _ = clamp_rect_position(
                left_x,
                anchor_y,
                reserve_w,
                self.height(),
                point=s.geometry().center(),
                fallback_widget=self,
            )
            if fx == left_x or abs(fx - left_x) < abs(x - right_x):
                x, y = fx, fy

        self.move(x, y)
        # 更新进度条位置
        self._update_progress_panel_position()
        # 更新翻页按钮位置
        update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())
        # 更新删除按钮位置
        self._update_remove_button_position()

    def _update_remove_button_position(self) -> None:
        """更新队列操作按钮位置（删除 + 立即播放）。"""
        if not self._visible:
            self._remove_btn.hide_btn()
            self._play_now_btn.hide_btn()
            return

        selected_abs = self._selected_abs_index()
        if selected_abs < 0:
            self._remove_btn.hide_btn()
            self._play_now_btn.hide_btn()
            return

        row = selected_abs - self._page * _PAGE_SIZE
        items = self._page_items()
        if not (0 <= row < len(items)):
            self._remove_btn.hide_btn()
            self._play_now_btn.hide_btn()
            return

        base_y = self.y() + _BORDER + row * _ROW_H + (_ROW_H - _REMOVE_BTN_H) // 2
        base_x = self.x() + self.width()
        remove_x, remove_y, _ = clamp_rect_position(
            base_x,
            base_y,
            _REMOVE_BTN_W,
            _REMOVE_BTN_H,
            point=self.geometry().center(),
            fallback_widget=self,
        )
        play_x, play_y, _ = clamp_rect_position(
            base_x + _REMOVE_BTN_W,
            base_y,
            _REMOVE_BTN_W,
            _REMOVE_BTN_H,
            point=self.geometry().center(),
            fallback_widget=self,
        )

        self._remove_btn.move(remove_x, remove_y)
        self._play_now_btn.move(play_x, play_y)
        self._remove_btn.show_btn()
        self._play_now_btn.show_btn()

    def _remove_selected_song(self) -> None:
        """???????????????????????????"""
        idx = self._selected_abs_index()
        if not (0 <= idx < len(self._queue)):
            self._update_remove_button_position()
            return

        song_id, _ = self._queue[idx]
        service = get_music_service()

        removed = False
        if idx == self._current_index:
            service.remove_song_from_history(song_id)
            service.next_track()
            removed = True
        else:
            removed = service.remove_queue_item(idx)
            if removed:
                service.remove_song_from_history(song_id)

        if not removed:
            return

        self._refresh_view_state(
            refresh_content=True,
            reset_page=False,
            preferred_index=idx,
            reposition=True,
        )

    def _play_selected_song(self) -> None:
        """立即播放当前高亮歌曲。"""
        idx = self._selected_abs_index()
        if not (0 <= idx < len(self._queue)):
            self._update_remove_button_position()
            return
        self._event_center.publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {
            'index': idx,
        }))

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
    # 私有：进度条管理
    # ==================================================================

    def _show_progress_panel(self) -> None:
        """显示进度条。"""
        from lib.script.ui.progress_panel import get_progress_panel
        progress_panel = get_progress_panel()
        if progress_panel:
            self._update_progress_panel_position()
            progress_panel.show_panel()

    def _hide_progress_panel(self) -> None:
        """隐藏进度条。"""
        from lib.script.ui.progress_panel import get_progress_panel
        progress_panel = get_progress_panel()
        if progress_panel:
            progress_panel.hide_panel()

    def _update_progress_panel_position(self) -> None:
        """更新进度条位置：左下锚点对齐播放列表的左上锚点（进度条在播放列表上方）。"""
        from lib.script.ui.progress_panel import get_progress_panel
        progress_panel = get_progress_panel()
        if progress_panel and self._visible:
            progress_panel.set_position_below_playlist(self.geometry())
        self._update_control_buttons_position()

    def _update_control_buttons_position(self) -> None:
        """更新控制按钮位置。"""
        if not self._visible:
            return
        from lib.script.ui.progress_panel import get_progress_panel
        progress_panel = get_progress_panel()
        if not progress_panel:
            return
        screen = get_screen_geometry_for_point(point=self.geometry().center(), fallback_widget=self)
        sx = screen.x()
        sy = screen.y()
        sw = screen.width()
        sh = screen.height()

        def _clamp(v: int, minimum: int, maximum: int) -> int:
            if maximum < minimum:
                return minimum
            return max(minimum, min(v, maximum))

        px = progress_panel.x()
        py = progress_panel.y()
        # 暂停按钮：左下锚点对齐进度条左上锚点，再上移2px
        pp_x = _clamp(px, sx, sx + sw - _BTN_WIDTH)
        pp_y = _clamp(py - _BTN_HEIGHT - scale_px(2, min_abs=1), sy, sy + sh - _BTN_HEIGHT)
        self._play_pause_btn.move(pp_x, pp_y)
        # 下一曲按钮：左锚点对齐暂停按钮右锚点
        nt_x = _clamp(pp_x + _BTN_WIDTH, sx, sx + sw - _BTN_WIDTH)
        self._next_track_btn.move(nt_x, pp_y)
        # 一键历史按钮：80px 宽，左锚点对齐下一曲按钮右锚点
        hq_x = _clamp(nt_x + _BTN_WIDTH, sx, sx + sw - _BTN_PLAYLIST_W)
        self._history_queue_btn.move(hq_x, pp_y)
        # 清空列表按钮：左下锚点对齐暂停按钮左上锚点
        cq_x = _clamp(pp_x, sx, sx + sw - _BTN_PLAYLIST_W)
        cq_y = _clamp(pp_y - _BTN_HEIGHT, sy, sy + sh - _BTN_HEIGHT)
        self._clear_queue_btn.move(cq_x, cq_y)
        # 一键本地按钮：左下锚点对齐一键历史按钮左上锚点
        lcl_x = _clamp(hq_x, sx, sx + sw - _BTN_PLAYLIST_W)
        lcl_y = _clamp(pp_y - _BTN_HEIGHT, sy, sy + sh - _BTN_HEIGHT)
        self._local_queue_btn.move(lcl_x, lcl_y)
        # 一键喜欢按钮：左下锚点对齐一键本地按钮左上锚点
        lq_x = _clamp(lcl_x, sx, sx + sw - _BTN_PLAYLIST_W)
        lq_y = _clamp(lcl_y - _BTN_HEIGHT, sy, sy + sh - _BTN_HEIGHT)
        self._liked_queue_btn.move(lq_x, lq_y)
        # 音量减按钮：右下锚点对齐进度条右上锚点，上移2px
        vd_x = _clamp(px + progress_panel.width() - _BTN_WIDTH, sx, sx + sw - _BTN_WIDTH)
        vd_y = pp_y
        self._volume_down_btn.move(vd_x, vd_y)
        # 音量加按钮：右锚点对齐音量减按钮左锚点
        vu_x = _clamp(vd_x - _BTN_WIDTH, sx, sx + sw - _BTN_WIDTH)
        self._volume_up_btn.move(vu_x, vd_y)
        # 播放模式按钮：右下锚点对齐音量减按钮右上锚点
        pm_x = _clamp(vd_x + _BTN_WIDTH - _BTN_PLAYLIST_W, sx, sx + sw - _BTN_PLAYLIST_W)
        pm_y = _clamp(vd_y - _BTN_HEIGHT, sy, sy + sh - _BTN_HEIGHT)
        self._play_mode_btn.move(pm_x, pm_y)

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_frame(self, event: Event) -> None:
        """帧事件处理：仅更新位置，不再轮询队列状态。"""
        if not self._visible or not self._focused_speaker:
            return
        # 音响消失时自动关闭
        if not self._focused_speaker.is_alive():
            self.hide_panel()
            return
        if self._is_mouse_far_from_family():
            self.hide_panel()
            return
        # 仅更新位置（不再每帧轮询队列）
        self._update_position()

    def _on_music_status(self, event: Event) -> None:
        """????????????????/???????????"""
        if not self._visible:
            return
        self._refresh_view_state(
            refresh_content=True,
            reset_page=False,
            update_remove_button=True,
        )
    
    def _on_song_end(self, event: Event) -> None:
        """??????????????"""
        if not self._visible:
            return
        self._refresh_view_state(
            refresh_content=True,
            reset_page=False,
            update_remove_button=True,
        )

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _iter_family_widgets(self):
        widgets = [
            self,
            self._prev_btn,
            self._next_btn,
            self._remove_btn,
            self._play_now_btn,
            *self._iter_control_buttons(),
        ]
        from lib.script.ui.progress_panel import get_progress_panel
        progress_panel = get_progress_panel()
        if progress_panel is not None:
            widgets.append(progress_panel)
        return [w for w in widgets if w is not None]

    def _is_mouse_far_from_family(self) -> bool:
        mouse = QCursor.pos()
        limit_sq = _AUTO_HIDE_MOUSE_DISTANCE * _AUTO_HIDE_MOUSE_DISTANCE
        nearest_sq = None
        for widget in self._iter_family_widgets():
            try:
                if not widget.isVisible():
                    continue
                center = widget.geometry().center()
                dx = mouse.x() - center.x()
                dy = mouse.y() - center.y()
                dist_sq = dx * dx + dy * dy
                if nearest_sq is None or dist_sq < nearest_sq:
                    nearest_sq = dist_sq
            except RuntimeError:
                continue
        if nearest_sq is None:
            return False
        return nearest_sq > limit_sq

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._font)

        # ── 三层边框（与命令提示框风格完全一致）────────────────────────
        painter.fillRect(self.rect(), COLORS['black'])
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), COLORS['cyan'])
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), COLORS['pink'])

        content_x = _BORDER
        content_w = self.width() - _BORDER * 2
        y         = _BORDER

        if not self._queue:
            # 空队列提示
            text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
            painter.setPen(COLORS['text'])
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, '（队列为空）')
        else:
            items = self._page_items()
            page_offset = self._page * _PAGE_SIZE
            for i, (_, display) in enumerate(items):
                row_rect  = QRect(content_x, y, content_w, _ROW_H)
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                is_current = (page_offset + i == self._current_index)
                # 搜索列表同款：高亮选中行；当前播放行保持青色标识
                if i == self._selected and not is_current:
                    painter.fillRect(row_rect, _C_HL)
                if is_current:
                    painter.fillRect(row_rect, COLORS['cyan'])
                prefix = '> ' if i == self._selected else ''
                label = prefix + ('♪ ' + display if is_current else display)
                painter.setPen(COLORS['text'])
                draw_mixed_text(
                    painter,
                    text_rect,
                    elide_mixed_text(label, text_rect.width(), self._font, self._digit_font),
                    self._font,
                    self._digit_font,
                    Qt.AlignLeft | Qt.AlignVCenter,
                )
                y += _ROW_H

            # 翻页指示器
            if self._has_pages():
                max_page  = (len(self._queue) - 1) // _PAGE_SIZE
                page_text = f'{self._page + 1}/{max_page + 1}'
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                painter.setPen(COLORS['text'])
                draw_mixed_text(
                    painter,
                    text_rect,
                    page_text,
                    self._font,
                    self._digit_font,
                    Qt.AlignCenter | Qt.AlignVCenter,
                )

        painter.end()

    def closeEvent(self, event) -> None:
        try:
            self._remove_btn.close()
        except Exception:
            pass
        try:
            self._play_now_btn.close()
        except Exception:
            pass
        super().closeEvent(event)


# ── 全局单例 ──────────────────────────────────────────────────────────
_instance: 'PlaylistPanel | None' = None


def get_playlist_panel() -> 'PlaylistPanel | None':
    """获取全局播放列表栏单例（未初始化时返回 None）。"""
    return _instance


def init_playlist_panel() -> 'PlaylistPanel':
    """初始化并返回全局播放列表栏单例，需在 Qt 主线程中调用。"""
    global _instance
    if _instance is None:
        _instance = PlaylistPanel()
    return _instance


def cleanup_playlist_panel():
    """释放全局播放列表栏资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None
