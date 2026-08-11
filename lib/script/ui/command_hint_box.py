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
from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFontMetrics, QPainter

from config.config import UI
from config.tooltip_config import TOOLTIPS
from lib.core.qt_bridge.font import get_digit_font, get_ui_font
from lib.core.graphics.application_visuals import (
    COMMAND_HINT_DEFAULT_ITEMS,
    COMMAND_HINT_PAGE_SIZE,
    CommandHintVisualDescription,
    build_command_hint_visual,
    command_hint_side_font_size,
)
from lib.core.graphics.types import FontSpec, Rect
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.qt_bridge.screen import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.qt_bridge.window import coerce_qpoint
from lib.script.ui.page_turn_buttons import make_page_buttons, update_page_buttons_position


_GAP_Y       = scale_px(2, min_abs=1)   # 与 CommandDialog 的垂直间距（px）

# ── 无输入时显示的默认提示行 ──────────────────────────────────────────
_DEFAULT_HINTS: list[str] = list(COMMAND_HINT_DEFAULT_ITEMS)


class _QtCommandHintTextMetrics:
    """Expose only low-level Qt glyph metrics to the shared presenter."""

    def __init__(self, default_font, digit_font, side_font) -> None:
        self._default_metrics = QFontMetrics(default_font)
        self._digit_metrics = QFontMetrics(digit_font)
        self._side_metrics = QFontMetrics(side_font)
        self.default_font = FontSpec(
            default_font.family(),
            default_font.pixelSize(),
            default_font.bold(),
        )
        self.digit_font = FontSpec(
            digit_font.family(),
            digit_font.pixelSize(),
            digit_font.bold(),
        )
        self.side_font = FontSpec(
            side_font.family(),
            side_font.pixelSize(),
            side_font.bold(),
        )
        self.default_ascent = float(self._default_metrics.ascent())
        self.default_descent = float(self._default_metrics.descent())
        self.digit_ascent = float(self._digit_metrics.ascent())
        self.digit_descent = float(self._digit_metrics.descent())

    def measure(
        self,
        text: str,
        *,
        digit: bool = False,
        side: bool = False,
    ) -> float:
        metrics = self._side_metrics if side else (
            self._digit_metrics if digit else self._default_metrics
        )
        return float(metrics.horizontalAdvance(str(text or "")))


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
        get_layer_manager().register(self, Layer.PET_UI)

        # ── 字体（粗体）────────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._side_label_font = get_digit_font(
            size=command_hint_side_font_size(self._font.pixelSize())
        )
        self._text_metrics = _QtCommandHintTextMetrics(
            self._font,
            self._digit_font,
            self._side_label_font,
        )
        self._draw_backend = QtDrawBackend()
        self._visual: CommandHintVisualDescription | None = None

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
            self._refresh_size()
            self.update()

    def turn_page(self, direction: int) -> None:
        """翻页：direction = -1（上一页）/ +1（下一页），支持循环翻页。"""
        if self._mode != 'hash' or not self._all_items:
            return
        max_page = max(0, (len(self._all_items) - 1) // COMMAND_HINT_PAGE_SIZE)
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
        start = self._page * COMMAND_HINT_PAGE_SIZE
        return self._all_items[start: start + COMMAND_HINT_PAGE_SIZE]

    def _has_pages(self) -> bool:
        return len(self._all_items) > COMMAND_HINT_PAGE_SIZE

    # ==================================================================
    # 私有：格式化与尺寸
    # ==================================================================

    def _refresh_size(self) -> None:
        """Rebuild the shared visual and apply its resolved window size."""
        self._visual = build_command_hint_visual(
            self._mode,
            self._all_items,
            self._selected,
            self._page,
            self._text_metrics,
        )
        self.setFixedSize(
            int(self._visual.size.width),
            int(self._visual.size.height),
        )

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
            new_pt = coerce_qpoint(event.data.get('anchor_point'))
            if new_pt is None:
                return
            if self._anchor_point != new_pt:
                self._anchor_point = new_pt
                self._update_position()

        elif ui_id == 'all' and window_id == 'command_dialog' and anchor_id == 'all':
            # CommandDialog 移动时的全局广播（anchor_point = 其左上角坐标）
            cmd_pos = coerce_qpoint(event.data.get('anchor_point'))
            if cmd_pos is None:
                return
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
        row = self._row_from_y(event.pos().y())
        if self._mode == 'default':
            if row != self._selected:
                self._selected = row
                self._refresh_size()
                self.update()
        elif self._mode == 'hash':
            items = self._page_items()
            if 0 <= row < len(items) and row != self._selected:
                self._selected = row
                self._refresh_size()
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
                row = self._row_from_y(event.pos().y())
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
        row_index = self._row_from_y(event.pos().y())
        page_rect = None if self._visual is None else self._visual.page_indicator_rect
        if row_index < 0 or row_index >= len(items):
            if page_rect is not None and self._rect_contains_y(page_rect, event.pos().y()):
                self.turn_page(-1 if event.pos().x() < self.width() // 2 else 1)
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
    def _rect_contains_y(rect: Rect, y: int) -> bool:
        return rect.y <= y < rect.y + rect.height

    def _row_from_y(self, y: int) -> int:
        visual = self._visual
        if visual is None:
            return -1
        for index, rect in enumerate(visual.row_rects):
            if self._rect_contains_y(rect, y):
                return index
        return -1

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        if self._visual is None:
            self._refresh_size()
        painter = QPainter(self)
        self._draw_backend.render(self._visual.batch, painter)
        painter.end()

    def closeEvent(self, event) -> None:
        for event_type, callback in (
            (EventType.FRAME, self._on_frame),
            (EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            self._event_center.unsubscribe(event_type, callback)
        self._draw_backend.cleanup()
        get_layer_manager().unregister(self)
        super().closeEvent(event)
