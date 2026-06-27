"""音响搜索结果框

跟随 SpeakerSearchDialog 底部显示音乐搜索结果列表。

布局：
  - 左上角对齐 SpeakerSearchDialog 的 bottom_left 锚点（+2px 间距）
  - 配色与搜索框一致：2px 黑外框 + 2px 灰白中框 + 棕色背景 + 纯白字体
  - 每页最多 5 条，超出时底部显示翻页指示器
  - 悬停高亮 + 左键点击立即播放
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QApplication, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter, QColor

from config.config import UI, UI_THEME, SPEAKER_SEARCH_UI
from config.tooltip_config import TOOLTIPS
from config.font_config import (
    get_digit_font,
    draw_mixed_text,
    get_ui_font,
    measure_mixed_text,
    elide_mixed_text,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position
from lib.script.ui.speaker_menu_style import (
    _C_BORDER,
    _C_MID,
    _C_BG,
    _C_TEXT,
    _C_HL,
    _LAYER,
    _BORDER,
    paint_speaker_menu_panel,
)

# ── 布局常量 ──────────────────────────────────────────────────────────
_PAGE_SIZE = 5     # 每页条目数
_ROW_H     = scale_px(20, min_abs=1)  # 每行高度（px）
_PAD_X     = scale_px(6, min_abs=1)   # 文字水平内边距（px）
_GAP_Y     = scale_px(2, min_abs=1)   # 与搜索框的垂直间距（px）
_MAX_WIDTH = scale_px(360, min_abs=1)  # 最大宽度（px）
_MIN_WIDTH = scale_px(240, min_abs=1)  # 最小宽度（与搜索框等宽，px）
_DIALOG_H  = SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1))  # 搜索框高度


class SpeakerSearchResultBox(QWidget):
    """
    音响搜索结果展示框。

    - 显示音乐抽象层返回的歌曲列表（最多 15 条，一页 5 条）
    - 悬停高亮，左键单击立即播放并关闭 UI
    - 跟随 SpeakerSearchDialog 移动（通过 UI_ANCHOR_RESPONSE 事件）
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
        self.setMouseTracking(True)
        get_topmost_manager().register(self)

        # ── 字体 ────────────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()

        # ── 状态 ────────────────────────────────────────────────────
        self._items: list[tuple[int | str, str]] = []   # [(track_ref, display_text)]
        self._selected: int   = -1
        self._page: int       = 0
        self._visible: bool   = False
        self._description     = TOOLTIPS['speaker_search_result_box']
        self._searching: bool = False

        self._anchor_point: QPoint | None = None

        # ── 翻页按钮 ──────────────────────────────────────────────────
        self._prev_btn, self._next_btn = make_page_buttons(
            lambda: self.turn_page(-1),
            lambda: self.turn_page(1),
        )

        # ── 透明度动画 ───────────────────────────────────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # ── 事件订阅 ─────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE,
                                     self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

        self._refresh_size()

    # ==================================================================
    # 公开接口（由 SpeakerSearchDialog 调用）
    # ==================================================================

    def clear_results(self) -> None:
        """???????"""
        self._items    = []
        self._selected = -1
        self._page     = 0
        self._refresh_view(reposition=False)

    def set_results(self, items: list) -> None:
        """?????????items: [(track_ref, display_text), ...]"""
        self._items    = items
        self._selected = 0 if items else -1
        self._page     = 0
        self._refresh_view()

    def set_searching(self, state: bool) -> None:
        """??"???"????? loading ?????"""
        self._searching = state
        self._refresh_view()

    def navigate(self, direction: int) -> None:
        """上下导航选中行（direction: -1 上 / +1 下）。"""
        if self._searching or not self._items:
            return
        items   = self._page_items()
        new_sel = self._selected + direction
        if 0 <= new_sel < len(items):
            self._selected = new_sel
            self.update()

    def turn_page(self, direction: int) -> None:
        """???direction: -1 ??? / +1 ????????????"""
        if self._searching or not self._items:
            return
        max_page = max(0, (len(self._items) - 1) // _PAGE_SIZE)
        if max_page == 0:
            return  # ??????????

        new_page = self._page + direction
        # ????????????????
        if new_page < 0:
            new_page = max_page
        elif new_page > max_page:
            new_page = 0

        self._page = new_page
        self._selected = 0
        self._refresh_view()

    def fade_in(self, dialog) -> None:
        """
        淡入显示（由 SpeakerSearchDialog 调用）。

        Args:
            dialog: SpeakerSearchDialog 实例，用于初始定位。
        """
        if self._visible:
            return
        self._visible = True
        # 断开旧的 fade-out 回调，防止误触发 hide()
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        # 根据对话框当前位置初始化锚点
        if dialog:
            self._anchor_point = QPoint(
                dialog.x(),
                dialog.y() + dialog.height(),
            )
        self._refresh_size()
        self.show()
        if self._anchor_point:
            self._update_position()
        self._animate(1.0)

    def fade_out(self) -> None:
        """淡出隐藏（随 SpeakerSearchDialog 消失时调用）。"""
        if not self._visible:
            return
        self._visible = False
        rect = self.geometry()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (rect.x(), rect.y(),
                            rect.x() + rect.width(), rect.y() + rect.height()),
        }))
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)
        self._prev_btn.hide_btn()
        self._next_btn.hide_btn()
    # ==================================================================

    def _page_items(self) -> list:
        start = self._page * _PAGE_SIZE
        return self._items[start: start + _PAGE_SIZE]

    def _has_pages(self) -> bool:
        return len(self._items) > _PAGE_SIZE

    def _refresh_size(self) -> None:
        """根据当前内容自适应窗口宽高。"""
        if self._searching:
            texts  = ['♪ 搜索中...']
            n_rows = 1
        elif not self._items:
            texts  = ['(无结果，请输入关键词后搜索)']
            n_rows = 1
        else:
            page_items = self._page_items()
            texts      = [t for _, t in page_items]
            n_rows     = len(page_items)
            if self._has_pages():
                max_page = (len(self._items) - 1) // _PAGE_SIZE
                texts.append(f'{self._page + 1}/{max_page + 1}')
                n_rows += 1

        max_text_w = max(
            (measure_mixed_text(t, self._font, self._digit_font) for t in texts),
            default=scale_px(60, min_abs=1),
        )
        w = int(max(_MIN_WIDTH, min(_MAX_WIDTH, max_text_w + _BORDER * 2 + _PAD_X * 2)))
        h = int(_BORDER * 2 + n_rows * _ROW_H)
        self.setFixedSize(w, h)

    # ==================================================================
    # 私有：位置与动画
    # ==================================================================

    def _refresh_view(self, *, reposition: bool = True) -> None:
        self._refresh_size()
        if reposition and self._visible:
            if self._anchor_point:
                self._update_position()
            else:
                update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())
        self.update()

    def _update_position(self) -> None:
        """将自身左上角对齐到搜索框底部 + _GAP_Y 偏移。"""
        if not self._anchor_point:
            return
        new_x = self._anchor_point.x()
        new_y = self._anchor_point.y() + _GAP_Y
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.width(),
            self.height(),
            point=self._anchor_point,
            fallback_widget=self,
        )
        self.move(x, y)
        update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())

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
    # 事件响应
    # ==================================================================

    def _on_anchor_response(self, event: Event) -> None:
        """监听 SpeakerSearchDialog 广播的位置更新，同步跟随。"""
        if not self._visible:
            return
        ui_id     = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == 'all' and window_id == 'speaker_search_dialog' and anchor_id == 'all':
            # anchor_point = dialog 左上角坐标
            dialog_pos = event.data.get('anchor_point')
            # 结果框跟随 dialog 底部
            new_pt = QPoint(dialog_pos.x(), dialog_pos.y() + _DIALOG_H)
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def mouseMoveEvent(self, event) -> None:
        """悬停时更新高亮行。"""
        if not self._searching and self._items:
            items = self._page_items()
            y_in  = event.pos().y() - _BORDER
            if y_in >= 0:
                row = y_in // _ROW_H
                if 0 <= row < len(items) and row != self._selected:
                    self._selected = row
                    self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        """
        左键：立即播放并关闭 UI。
        右键：加入播放队列，UI 保持打开。
        """
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if self._searching or not self._items:
            super().mousePressEvent(event)
            return

        items = self._page_items()
        y_in  = event.pos().y() - _BORDER
        if y_in < 0:
            super().mousePressEvent(event)
            return

        row = y_in // _ROW_H
        if row >= len(items):
            super().mousePressEvent(event)
            return

        track_ref, display = items[row]

        if event.button() == Qt.LeftButton:
            # 置顶播放：通过事件系统触发（解耦 UI 与播放逻辑）
            self._event_center.publish(Event(EventType.MUSIC_PLAY_TOP, {
                'song_id': track_ref,
                'track_ref': track_ref,
                'display': display,
            }))

        elif event.button() == Qt.RightButton:
            # 加入队列末尾：UI 保持打开，通过事件系统触发
            self._event_center.publish(Event(EventType.MUSIC_ENQUEUE, {
                'song_id': track_ref,
                'track_ref': track_ref,
                'display': display,
            }))

        super().mousePressEvent(event)

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setFont(self._font)
        paint_speaker_menu_panel(p, self.rect())

        content_x = _BORDER
        content_w = self.width() - _BORDER * 2
        y = _BORDER

        if self._searching:
            # 搜索中提示
            text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
            p.setPen(_C_TEXT)
            p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, '♪ 搜索中...')

        elif not self._items:
            # 空状态提示
            text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
            p.setPen(_C_TEXT)
            p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                       '(无结果，请输入关键词后搜索)')

        else:
            # 歌曲列表
            items = self._page_items()
            for i, (_sid, display) in enumerate(items):
                row_rect  = QRect(content_x, y, content_w, _ROW_H)
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                # 高亮选中行
                if i == self._selected:
                    p.fillRect(row_rect, _C_HL)
                p.setPen(_C_TEXT)
                draw_mixed_text(
                    p, text_rect,
                    elide_mixed_text(display, text_rect.width(), self._font, self._digit_font),
                    self._font, self._digit_font,
                )
                y += _ROW_H

            # 翻页指示器
            if self._has_pages():
                max_page  = (len(self._items) - 1) // _PAGE_SIZE
                page_text = f'{self._page + 1}/{max_page + 1}'
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                p.setPen(_C_TEXT)
                draw_mixed_text(p, text_rect, page_text, self._font, self._digit_font, Qt.AlignCenter)

        p.end()
