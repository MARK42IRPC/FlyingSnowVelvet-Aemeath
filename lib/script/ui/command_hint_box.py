"""命令提示框 - 在输入框正下方显示操作提示和 # 命令实时补全列表

布局：
  - 左上锚点对齐 CommandDialog 的 bottom_left 锚点（+2px 间距）
  - 绘制风格与输入框一致：2px 黑色外框 + 2px 青色中框 + 粉色内背景
  - 文字左对齐，自适应宽度，最大 360px

显示逻辑：
  - 无输入 / 非 # 输入 → 默认提示（3 条静态说明行）
  - # 输入时 → 过滤 # 命令列表，支持 Tab 补全 / ↑↓ 导航 / ←→ 翻页
  - 每页最多 5 条，超出时底部显示页码指示器
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QApplication, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFontMetrics, QPainter

from config.config import COLORS, UI, UI_THEME
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
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position


# ── 布局常量 ──────────────────────────────────────────────────────────
_MAX_WIDTH   = scale_px(360, min_abs=1)  # 最大宽度（px）
_MIN_WIDTH   = scale_px(240, min_abs=1)  # 最小宽度（px）
_PAGE_SIZE   = 5     # 每页最大条目数
_ROW_H       = scale_px(20, min_abs=1)  # 每行高度（px）
_LAYER       = scale_px(2, min_abs=1)
_BORDER      = _LAYER * 2  # 单侧边框总厚度（2px 黑 + 2px 青）
_PAD_X       = scale_px(6, min_abs=1)   # 文字水平内边距（px）
_GAP_Y       = scale_px(2, min_abs=1)   # 与 CommandDialog 的垂直间距（px）
# 默认模式行间分隔线（仅在三条提示行之间绘制）
_SEP_CYAN_H  = scale_px(5, min_abs=1)  # 浅青色分隔段高度（px）
_SEP_BLACK_H = scale_px(1, min_abs=1)  # 黑色分隔段高度（px）
_SEP_H       = _SEP_CYAN_H                 # 分隔线总占高 = 5px（黑线浮于青色带中心，不额外占高）

# ── 无输入时显示的默认提示行 ──────────────────────────────────────────
_DEFAULT_HINTS: list[str] = [
    '/-在CMD窗口中执行命令',
    '#-执行玩法命令',
    '聊天-与爱弥斯聊天',
]
_DEFAULT_SIDE_LABEL = 'Aemeath'
_DEFAULT_SIDE_LABEL_HIGHLIGHT = 'RUNcmd'
_SIDE_LABEL_GAP_X = scale_px(8, min_abs=1)
_SIDE_LABEL_PAD_R = scale_px(6, min_abs=1)
_SIDE_LABEL_FONT_BOOST = scale_px(3, min_abs=1)


class CommandHintBox(QWidget):
    """
    命令提示框（右键 UI 组件）。

    - 无输入 / 非 # 输入时：显示三条通用操作提示（静态）
    - # 输入时：实时过滤并展示匹配的 # 命令列表
      · Tab     → 自动补全当前选中命令
      · ↑ ↓    → 切换选中行
      · ← →    → 翻页（游标位于行首 / 行尾时才触发）
    - 跟随 CommandDialog 的 bottom_left 锚点
    - 淡入淡出 + right_fade 粒子消散特效（与其余右键 UI 一致）
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 不抢夺键盘焦点（避免点击时导致输入框失焦）
        self.setFocusPolicy(Qt.NoFocus)
        # 启用鼠标追踪，支持悬停高亮
        self.setMouseTracking(True)
        get_topmost_manager().register(self)

        # ── 字体（粗体）────────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._side_label_font = get_digit_font(
            size=max(self._font.pixelSize() + _SIDE_LABEL_FONT_BOOST, scale_px(14, min_abs=1))
        )
        self._side_label_color = UI_THEME['deep_pink']
        self._side_label_highlight_color = UI_THEME['deep_cyan']

        # ── 状态 ──────────────────────────────────────────────────────
        self._mode: str       = 'default'  # 'default' | 'hash'
        self._all_items: list = []         # 全部条目（用于翻页计算）
        self._selected: int   = -1         # 当前选中行在当前页中的索引（-1 = 无）
        self._page: int       = 0          # 当前页码（0-based）
        self._visible: bool   = False
        self._description     = TOOLTIPS['command_hint_box']
        self._anchor_available: bool = False

        # ── 透明度动画（与其他右键 UI 统一使用相同时长）────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # ── 锚点（= CommandDialog bottom_left 的全局屏幕坐标）──────────
        self._anchor_point: QPoint | None = None

        # ── 翻页按钮 ──────────────────────────────────────────────────
        self._prev_btn, self._next_btn = make_page_buttons(
            lambda: self.turn_page(-1),
            lambda: self.turn_page(1),
        )

        # ── 事件订阅 ──────────────────────────────────────────────────
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME,                    self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE,       self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,   self._on_clickthrough_toggle)

        # 初始内容与尺寸
        self._set_default_mode()
        self._refresh_size()

    # ==================================================================
    # 公开接口（由 CommandDialog 调用）
    # ==================================================================

    def update_input(self, text: str) -> None:
        """输入框文本变化时更新提示内容（由 _entry.textChanged 驱动）。"""
        if text.startswith('#'):
            self._set_hash_mode(text[1:])
        else:
            self._set_default_mode()
        self._refresh_size()
        if self._visible and self._anchor_available and self._anchor_point:
            self._update_position()
        self.update()

    def get_completion(self) -> str:
        """
        返回当前选中命令的补全字符串（含 # 前缀和尾部空格）。
        仅 hash 模式且有有效选中项时非空。
        """
        if self._mode != 'hash' or self._selected < 0:
            return ''
        items = self._page_items()
        if 0 <= self._selected < len(items):
            name = items[self._selected][0]
            return f'#{name} '
        return ''

    def navigate(self, direction: int) -> None:
        """上下导航：direction = -1（上）/ +1（下）。"""
        if self._mode != 'hash':
            return
        items = self._page_items()
        if not items:
            return
        new_sel = self._selected + direction
        if 0 <= new_sel < len(items):
            self._selected = new_sel
            self.update()

    def turn_page(self, direction: int) -> None:
        """翻页：direction = -1（上一页）/ +1（下一页），支持循环翻页。"""
        if self._mode != 'hash' or not self._all_items:
            return
        max_page = max(0, (len(self._all_items) - 1) // _PAGE_SIZE)
        if max_page == 0:
            return  # 只有一页，不需要翻页

        new_page = self._page + direction
        # 循环翻页：超出范围时跳转到另一端
        if new_page < 0:
            new_page = max_page
        elif new_page > max_page:
            new_page = 0

        self._page     = new_page
        self._selected = 0
        self._refresh_size()
        if self._visible and self._anchor_available and self._anchor_point:
            self._update_position()
        self.update()

    def fade_in(self) -> None:
        """淡入显示（随 CommandDialog 出现时调用）。"""
        if self._visible:
            return
        self._visible         = True
        self._anchor_available = True
        # 取消已挂载的 fade-out 回调，防止旧动画结束时误触发 hide()
        try:
            self._anim.finished.disconnect(self._on_fade_out_done)
        except (RuntimeError, TypeError):
            pass
        self._set_default_mode()
        self._refresh_size()
        self.show()
        # 申请 CommandDialog 的 bottom_left 锚点
        self._event_center.publish(Event(EventType.UI_CREATE, {
            'window_id': 'command_dialog',
            'anchor_id': 'bottom_left',
            'ui_id':     'command_hint_box',
        }))
        self._animate(1.0)

    def fade_out(self) -> None:
        """淡出隐藏，同时发射 right_fade 粒子（随 CommandDialog 消失时调用）。"""
        if not self._visible:
            return
        self._visible         = False
        self._anchor_available = False
        # right_fade 消散特效（与 CloseButton 等一致）
        rect = self.geometry()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type':   'rect',
            'area_data':   (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()),
        }))
        self._anim.finished.connect(self._on_fade_out_done)
        self._animate(0.0)
        self._prev_btn.hide_btn()
        self._next_btn.hide_btn()

    # ==================================================================
    # 私有：模式切换
    # ==================================================================

    def _set_default_mode(self) -> None:
        self._mode      = 'default'
        self._all_items = list(_DEFAULT_HINTS)
        self._selected  = 0 if self._all_items else -1
        self._page      = 0

    def _set_hash_mode(self, query: str) -> None:
        self._mode      = 'hash'
        self._all_items = get_hash_cmd_registry().filter(query)
        self._selected  = 0 if self._all_items else -1
        self._page      = 0

    def _page_items(self) -> list:
        start = self._page * _PAGE_SIZE
        return self._all_items[start: start + _PAGE_SIZE]

    def _has_pages(self) -> bool:
        return len(self._all_items) > _PAGE_SIZE

    # ==================================================================
    # 私有：格式化与尺寸
    # ==================================================================

    @staticmethod
    def _fmt_hash(item: tuple) -> str:
        """将 (name, usage, desc) 格式化为显示字符串。"""
        name, usage, desc = item
        text = f'#{name}'
        if usage:
            text += f' {usage}'
        if desc:
            text += f'  {desc}'
        return text

    def _default_side_label_width(self) -> int:
        fm = QFontMetrics(self._side_label_font)
        return max(
            fm.horizontalAdvance(_DEFAULT_SIDE_LABEL),
            fm.horizontalAdvance(_DEFAULT_SIDE_LABEL_HIGHLIGHT),
        )

    def _default_side_label_reserve_width(self) -> int:
        return int(self._default_side_label_width() + _SIDE_LABEL_GAP_X + _SIDE_LABEL_PAD_R)

    def _refresh_size(self) -> None:
        """根据当前内容自适应窗口宽高。"""
        items = self._page_items()

        if self._mode == 'default':
            measure_texts = list(self._all_items)
            n_rows        = len(self._all_items)
        else:
            if items:
                measure_texts = [self._fmt_hash(it) for it in items]
                n_rows        = len(items)
            else:
                measure_texts = ['(无匹配命令)']
                n_rows        = 1
            if self._has_pages():
                max_page = (len(self._all_items) - 1) // _PAGE_SIZE
                measure_texts.append(f'◀ {self._page + 1}/{max_page + 1} ▶')
                n_rows += 1

        max_text_w = max(
            (measure_mixed_text(t, self._font, self._digit_font) for t in measure_texts),
            default=scale_px(60, min_abs=1),
        )
        if self._mode == 'default':
            max_text_w += self._default_side_label_reserve_width()
        w = int(max(_MIN_WIDTH, min(_MAX_WIDTH, max_text_w + _BORDER * 2 + _PAD_X * 2)))
        if self._mode == 'default':
            n_items = len(self._all_items)
            h = int(_BORDER * 2 + n_items * _ROW_H + max(0, n_items - 1) * _SEP_H)
        else:
            h = int(_BORDER * 2 + n_rows * _ROW_H)
        self.setFixedSize(w, h)

    def _update_position(self) -> None:
        """将自身左上角对齐到 CommandDialog bottom_left + _GAP_Y 偏移。"""
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
        if self.x() != x or self.y() != y:
            self.move(x, y)
        update_page_buttons_position(self, self._prev_btn, self._next_btn, self._has_pages())

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
        # 仅在确实处于隐藏状态时才调用 hide()，防止 fade_in 后被错误隐藏
        if not self._visible:
            self.hide()

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _on_frame(self, event: Event) -> None:
        if self._visible and self._anchor_available and self._anchor_point:
            self._update_position()

    def _on_anchor_response(self, event: Event) -> None:
        if not self._anchor_available:
            return
        ui_id     = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == 'command_hint_box':
            # CommandDialog 对 bottom_left 请求的直接响应
            new_pt = event.data.get('anchor_point')
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

        elif ui_id == 'all' and window_id == 'command_dialog' and anchor_id == 'all':
            # CommandDialog 移动时的全局广播（anchor_point = 其左上角坐标）
            cmd_pos = event.data.get('anchor_point')
            cmd_h   = UI['cmd_window_height']
            new_pt  = QPoint(cmd_pos.x(), cmd_pos.y() + cmd_h)
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def mouseMoveEvent(self, event) -> None:
        """鼠标悬停时实时更新高亮行，便于直观点击。"""
        if self._mode == 'default':
            y_in = event.pos().y() - _BORDER
            row = self._default_row_from_y(y_in)
            if row != self._selected:
                self._selected = row
                self.update()
        elif self._mode == 'hash':
            items = self._page_items()
            y_in_content = event.pos().y() - _BORDER
            if y_in_content >= 0:
                row_index = y_in_content // _ROW_H
                if 0 <= row_index < len(items) and row_index != self._selected:
                    self._selected = row_index
                    self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        """
        左键点击条目：
        - 默认模式：填充命令前缀
        - # 模式：执行命令（不关闭 UI）
        点击翻页指示器左/右半区翻页。
        """
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if self._mode == 'default':
            if event.button() == Qt.LeftButton:
                y_in = event.pos().y() - _BORDER
                row = self._default_row_from_y(y_in)
                if row == 0:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '/'}))
                elif row == 1:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '#'}))
                elif row == 2:
                    self._event_center.publish(Event(EventType.UI_HINT_PICK, {'text': '你好啊,爱弥斯'}))
            super().mousePressEvent(event)
            return

        if self._mode != 'hash':
            super().mousePressEvent(event)
            return

        items = self._page_items()
        y_in = event.pos().y() - _BORDER
        if y_in < 0:
            super().mousePressEvent(event)
            return

        row_index = y_in // _ROW_H
        if row_index >= len(items):
            # 点击翻页指示器行：左半区上一页，右半区下一页
            if self._has_pages() and row_index == len(items):
                if event.pos().x() < self.width() // 2:
                    self.turn_page(-1)
                else:
                    self.turn_page(1)
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton:
            # 执行命令：通过事件系统触发（解耦 UI 与命令逻辑）
            name = items[row_index][0]
            self._event_center.publish(Event(EventType.INPUT_HASH, {
                'text': name,
                'raw':  f'#{name}',
            }))

        super().mousePressEvent(event)

    @staticmethod
    def _default_row_from_y(y_in: int) -> int:
        """默认模式下根据 y 坐标定位提示行索引。"""
        if y_in < 0:
            return -1
        cursor = 0
        for i in range(len(_DEFAULT_HINTS)):
            if cursor <= y_in < cursor + _ROW_H:
                return i
            cursor += _ROW_H
            if i < len(_DEFAULT_HINTS) - 1:
                if cursor <= y_in < cursor + _SEP_H:
                    return -1
                cursor += _SEP_H
        return -1

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setFont(self._font)
        fm = painter.fontMetrics()

        # ── 三层边框（与 CommandDialog 风格一致）──────────────────────
        painter.fillRect(self.rect(), COLORS['black'])
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), COLORS['cyan'])
        painter.fillRect(self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER), COLORS['pink'])

        # ── 绘制行内容 ────────────────────────────────────────────────
        items     = self._page_items()
        content_x = _BORDER
        content_w = self.width() - _BORDER * 2
        y         = _BORDER

        if self._mode == 'default':
            side_label_w = self._default_side_label_width()
            side_reserve_w = self._default_side_label_reserve_width()
            for i, hint in enumerate(self._all_items):
                row_rect = QRect(content_x, y, content_w, _ROW_H)
                side_rect = QRect(
                    content_x + content_w - _SIDE_LABEL_PAD_R - side_label_w,
                    y,
                    side_label_w,
                    _ROW_H,
                )
                text_rect = QRect(
                    content_x + _PAD_X,
                    y,
                    max(0, content_w - _PAD_X * 2 - side_reserve_w),
                    _ROW_H,
                )
                if i == self._selected:
                    painter.fillRect(row_rect, COLORS['cyan'])
                painter.setFont(self._font)
                painter.setPen(COLORS['text'])
                painter.drawText(
                    text_rect,
                    Qt.AlignLeft | Qt.AlignVCenter,
                    fm.elidedText(hint, Qt.ElideRight, text_rect.width()),
                )
                painter.setFont(self._side_label_font)
                painter.setPen(self._side_label_highlight_color if i == self._selected else self._side_label_color)
                painter.drawText(
                    side_rect,
                    Qt.AlignRight | Qt.AlignVCenter,
                    _DEFAULT_SIDE_LABEL_HIGHLIGHT if i == self._selected else _DEFAULT_SIDE_LABEL,
                )
                y += _ROW_H
                # 行间分隔线（最后一行后不绘制）
                if i < len(self._all_items) - 1:
                    painter.fillRect(QRect(content_x, y, content_w, _SEP_CYAN_H), COLORS['cyan'])
                    # 黑色细线居中于青色带，横跨全宽与最外层黑框相连
                    black_y = y + (_SEP_CYAN_H - _SEP_BLACK_H) // 2
                    painter.fillRect(QRect(0, black_y, self.width(), _SEP_BLACK_H), COLORS['black'])
                    y += _SEP_H
        else:
            if not items:
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                painter.setPen(COLORS['text'])
                painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, '(无匹配命令)')
                y += _ROW_H
            else:
                for i, item in enumerate(items):
                    row_rect  = QRect(content_x, y, content_w, _ROW_H)
                    text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                    # 选中行：青色背景高亮
                    if i == self._selected:
                        painter.fillRect(row_rect, COLORS['cyan'])
                    text = self._fmt_hash(item)
                    painter.setPen(COLORS['text'])
                    draw_mixed_text(
                        painter, text_rect,
                        elide_mixed_text(text, text_rect.width(), self._font, self._digit_font),
                        self._font, self._digit_font,
                    )
                    y += _ROW_H

            # ── 翻页指示器 ────────────────────────────────────────────
            if self._has_pages():
                max_page  = (len(self._all_items) - 1) // _PAGE_SIZE
                page_text = f'{self._page + 1}/{max_page + 1}'
                text_rect = QRect(content_x + _PAD_X, y, content_w - _PAD_X * 2, _ROW_H)
                painter.setPen(COLORS['text'])
                draw_mixed_text(painter, text_rect, page_text, self._font, self._digit_font, Qt.AlignCenter)

        painter.end()
