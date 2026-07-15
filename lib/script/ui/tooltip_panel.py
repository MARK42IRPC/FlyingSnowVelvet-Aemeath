"""说明书（悬停提示面板）

当鼠标在任意 UI 组件上静止超过 20 tick（约 1 秒）时，
读取该组件的 _description 属性并在鼠标右侧淡入显示。
鼠标移动时立即淡出（无粒子特效）。

绘制风格：1px 黑边 + 1px 蓝色内框 + 粉色背景。
"""
from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QApplication, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter, QFontMetrics, QCursor

from config.config import COLORS, UI, UI_THEME
from config.font_config import (
    get_ui_font,
    get_digit_font,
    draw_mixed_text,
    wrap_mixed_text,
    measure_mixed_text,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.anchor_utils import apply_ui_opacity

# ── 布局常量 ──────────────────────────────────────────────────────────
_LAYER       = scale_px(1, min_abs=1)
_BORDER      = _LAYER * 2  # 1px 黑边 + 1px 蓝边
_PAD_X       = scale_px(6, min_abs=1)   # 文字水平内边距
_PAD_Y       = scale_px(3, min_abs=1)   # 文字垂直内边距
_MAX_TEXT_W  = scale_px(220, min_abs=1)  # 文字区最大宽度（px），超出自动换行
_CURSOR_GAP  = scale_px(10, min_abs=1)   # 面板左边与光标的间距（px）
_HOVER_TICKS = 20    # 静止多少 tick 后显示（20 tick = 1s @20tick/s）


def _tooltip_target_opacity() -> float:
    try:
        value = float(UI.get('tooltip_opacity', 0.8))
    except Exception:
        value = 0.8
    return max(0.0, min(1.0, value))


class TooltipPanel(QWidget):
    """鼠标悬停说明书面板 —— 全局单例。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # 不拦截鼠标
        self.setAttribute(Qt.WA_ShowWithoutActivating)       # 不抢焦点
        get_layer_manager().register(self, Layer.TOOLTIP)

        # ── 透明度动画 ────────────────────────────────────────────────
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        # ── 字体 ─────────────────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()

        # ── 悬停状态 ─────────────────────────────────────────────────
        self._visible          = False
        self._last_pos: QPoint = QCursor.pos()
        self._stationary_ticks = 0
        self._current_text     = ''

        # ── 事件订阅 ─────────────────────────────────────────────────
        self._ec = get_event_center()
        self._ec.subscribe(EventType.TICK, self._on_tick)

    # ==================================================================
    # Tick 驱动的悬停检测
    # ==================================================================

    def _on_tick(self, event: Event) -> None:
        current_pos = QCursor.pos()

        if current_pos != self._last_pos:
            # 鼠标移动 → 重置计数，隐藏面板
            self._last_pos         = current_pos
            self._stationary_ticks = 0
            if self._visible:
                self._hide()
        else:
            # 鼠标静止 → 累计
            if self._stationary_ticks < _HOVER_TICKS:
                self._stationary_ticks += 1
            if self._stationary_ticks == _HOVER_TICKS and not self._visible:
                desc = self._find_description(current_pos)
                if desc:
                    self._show(desc, current_pos)

    # ==================================================================
    # 查找说明字段
    # ==================================================================

    def _find_description(self, global_pos: QPoint) -> str:
        """从光标下的 widget 开始查找 _description。

        策略：
        1. widgetAt 找到鼠标下的 widget
        2. 向上遍历 parent() 链（处理子 widget 情况）
        3. 若 parent() 链断裂（PyQt5 有时返回 C++ 包装而非 Python 实例），
           改为遍历所有顶层窗口，检查哪个窗口包含该 widget，
           直接在顶层窗口上查找 _description
        """
        widget = QApplication.widgetAt(global_pos)

        # 无焦点时 widgetAt 可能返回 None，手动从顶层窗口做命中测试
        if widget is None:
            for top in reversed(QApplication.topLevelWidgets()):
                if top is self or not top.isVisible():
                    continue
                local = top.mapFromGlobal(global_pos)
                hit = top.childAt(local)
                if hit is not None:
                    widget = hit
                    break
                if top.rect().contains(local):
                    widget = top
                    break

        if widget is None:
            return ''

        # 先尝试 parent() 链（widget 本身 → 父级 → 祖父级 …）
        cur = widget
        while cur is not None:
            if cur is self:
                break
            desc = getattr(cur, '_description', None)
            if desc:
                return str(desc)
            cur = cur.parent()

        # parent() 链未找到时，回退到遍历顶层窗口：
        # 找到包含 widget 的那个顶层窗口，直接在其上查找 _description
        for top in QApplication.topLevelWidgets():
            if top is self or not top.isVisible():
                continue
            # 判断 widget 是否属于这个顶层窗口
            local = top.mapFromGlobal(global_pos)
            if top.rect().contains(local):
                desc = getattr(top, '_description', None)
                if desc:
                    return str(desc)

        return ''

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def _show(self, text: str, cursor_pos: QPoint) -> None:
        self._current_text = text
        self._recalc_size()
        self._reposition(cursor_pos)
        self.show()
        self._visible = True
        self._animate(1.0)

    def _hide(self) -> None:
        self._visible = False
        self._animate(0.0)

    def hide_now(self, *, reset_hover: bool = True) -> None:
        """立即隐藏提示框，并可选重置悬停计时状态。"""
        self._anim.stop()
        self._visible = False
        self.hide()
        self._opacity.setOpacity(0.0)
        if reset_hover:
            self._stationary_ticks = 0
            self._last_pos = QCursor.pos()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(self._opacity.opacity()))
        self._anim.setEndValue(apply_ui_opacity(target * _tooltip_target_opacity()))
        self._anim.start()

    def _on_anim_finished(self) -> None:
        """淡出完成后隐藏窗口，避免占用 z-order。"""
        if not self._visible:
            self.hide()

    # ==================================================================
    # 布局计算
    # ==================================================================

    def _recalc_size(self) -> None:
        """依据文本内容重新计算面板尺寸。"""
        lines  = self._wrap_text(self._current_text)
        fm_def = QFontMetrics(self._font)
        fm_dig = QFontMetrics(self._digit_font)
        line_h = max(fm_def.height(), fm_dig.height())
        text_w = max(
            (measure_mixed_text(ln, self._font, self._digit_font) for ln in lines),
            default=scale_px(40, min_abs=1),
        )
        text_h = line_h * len(lines)
        self.setFixedSize(
            text_w + _PAD_X * 2 + _BORDER * 2,
            text_h + _PAD_Y * 2 + _BORDER * 2,
        )

    def _reposition(self, cursor_pos: QPoint) -> None:
        """将面板放在光标右侧；超出屏幕右/下边界时自动镜像。"""
        screen = get_screen_geometry_for_point(point=cursor_pos, fallback_widget=self)
        x = cursor_pos.x() + _CURSOR_GAP
        y = cursor_pos.y()
        if x + self.width() > screen.x() + screen.width():
            x = cursor_pos.x() - self.width() - _CURSOR_GAP
        x, y, _ = clamp_rect_position(
            x,
            y,
            self.width(),
            self.height(),
            point=cursor_pos,
            fallback_widget=self,
        )
        self.move(x, y)

    def _wrap_text(self, text: str) -> list[str]:
        """按 _MAX_TEXT_W 像素宽度对文本进行自动换行。"""
        return wrap_mixed_text(text, _MAX_TEXT_W, self._font, self._digit_font)

    # ==================================================================
    # 绘制
    # ==================================================================

    def paintEvent(self, event) -> None:
        if not self._current_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 轻量三层边框：黑 → 蓝 → 粉
        painter.fillRect(self.rect(), COLORS['black'])
        painter.fillRect(self.rect().adjusted(_LAYER, _LAYER, -_LAYER, -_LAYER), UI_THEME['deep_cyan'])
        inner_rect = self.rect().adjusted(_BORDER, _BORDER, -_BORDER, -_BORDER)
        painter.fillRect(inner_rect, COLORS['pink'])

        # 文字区域
        content = self.rect().adjusted(
            _BORDER + _PAD_X, _BORDER + _PAD_Y,
            -_BORDER - _PAD_X, -_BORDER - _PAD_Y,
        )
        lines = self._wrap_text(self._current_text)
        line_h = max(
            QFontMetrics(self._font).height(),
            QFontMetrics(self._digit_font).height(),
        )

        painter.setPen(COLORS['black'])
        painter.setFont(self._font)
        for i, ln in enumerate(lines):
            line_rect = content.adjusted(0, i * line_h, 0, 0)
            line_rect.setHeight(line_h)
            draw_mixed_text(
                painter,
                line_rect,
                ln,
                self._font,
                self._digit_font,
                Qt.AlignLeft | Qt.AlignVCenter,
            )

        painter.end()


# ==================================================================
# 全局单例管理
# ==================================================================

_instance: TooltipPanel | None = None


def get_tooltip_panel() -> TooltipPanel | None:
    return _instance


def init_tooltip_panel() -> TooltipPanel:
    global _instance
    if _instance is None:
        _instance = TooltipPanel()
    return _instance


def cleanup_tooltip_panel() -> None:
    """释放全局说明书面板资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None
