"""说明书（悬停提示面板）

当鼠标在任意 UI 组件上静止超过 20 tick（约 1 秒）时，
读取该组件的 _description 属性并在鼠标右侧淡入显示。
鼠标移动时立即淡出（无粒子特效）。

绘制风格：1px 黑边 + 1px 蓝色内框 + 粉色背景。

本类不再继承 ``QWidget``：悬停计数、文本排版与位置解算在
``lib/core/render/visuals/controls.py`` 的 ``TooltipControl`` / ``TooltipHoverState`` 里，
真实窗口（窗口标志、透明度动画、自动隐藏计时、绘制执行）由后端窗口宿主
（``backends/qt/widgets/control_host.py``）持有。控件自身只负责事件订阅、把描述交给宿主，
以及"光标下面到底是哪个控件"这层 Qt 查询。
"""
from __future__ import annotations

from config.config import UI
from lib.core.render.visuals.controls import (
    TOOLTIP_HIDE,
    TOOLTIP_SHOW,
    TooltipControl,
)
from lib.script.ui.render_bridge import (
    create_control_host,
    digit_font as get_digit_font,
    pointer_cursor,
    screen_rect_for_point as get_screen_geometry_for_point,
    text_metrics as QtTextMetrics,
    ui_font as get_ui_font,
)
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer
from lib.core.anchor_utils import apply_ui_opacity

# ── 布局常量 ───────────────────────────────────────────────────────────
_LAYER       = scale_px(1, min_abs=1)
_BORDER      = _LAYER * 2  # 1px 黑边 + 1px 蓝边
_PAD_X       = scale_px(6, min_abs=1)   # 文字水平内边距
_PAD_Y       = scale_px(3, min_abs=1)   # 文字垂直内边距
_MAX_TEXT_W  = scale_px(220, min_abs=1)  # 文字区最大宽度（px），超出自动换行
_CURSOR_GAP  = scale_px(10, min_abs=1)   # 面板左边与光标的间距（px）
_HOVER_TICKS = 20    # 静止多少 tick 后显示（20 tick = 1s @20tick/s）
_AUTO_HIDE_MS = 5000


def _tooltip_target_opacity() -> float:
    try:
        value = float(UI.get('tooltip_opacity', 0.8))
    except Exception:
        value = 0.8
    return max(0.0, min(1.0, value))


class TooltipPanel:
    """鼠标悬停说明书面板 —— 全局单例。"""

    def __init__(self):
        # ── 悬停起点：描述层以构造时的光标位置为静止计数起点 ────────
        self._last_pos = pointer_cursor()

        # ── 字体与描述层 ──────────────────────────────────────────
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._text_metrics = QtTextMetrics(self._font, self._digit_font)
        self._control = TooltipControl(
            self._text_metrics,
            max_text_width=_MAX_TEXT_W,
            padding_x=_PAD_X,
            padding_y=_PAD_Y,
            border_width=_LAYER,
            cursor_gap=_CURSOR_GAP,
            min_text_width=scale_px(40, min_abs=1),
            paint_layer=int(Layer.TOOLTIP),
            opacity_scale=lambda: apply_ui_opacity(1.0),
            hover_ticks=_HOVER_TICKS,
            auto_hide_ms=_AUTO_HIDE_MS,
            initial_position=self._last_pos,
        )

        # ── 宿主（真实窗口）───────────────────────────────────────
        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_fade_out_finished=self._on_fade_out_finished,
            auto_hide_ms=_AUTO_HIDE_MS,
            on_auto_hide=self._hide,
            layer=Layer.TOOLTIP,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=UI['ui_fade_duration'],
            transparent_for_mouse=True,     # 不拦截鼠标
            show_without_activating=True,   # 不抢焦点
        )
        self._description = ''

        # ── 悬停状态（描述层共享）─────────────────────────────────
        self._visible = False
        self._current_text = ''

        # ── 事件订阅 ───────────────────────────────────────────────
        self._ec = get_event_center()
        self._ec.subscribe(EventType.TICK, self._on_tick)

    # ==================================================================
    # 描述状态（测试与内部逻辑读取的稳定入口）
    # ==================================================================
    @property
    def _stationary_ticks(self) -> int:
        return self._control.hover.stationary_ticks

    @_stationary_ticks.setter
    def _stationary_ticks(self, value: int) -> None:
        self._control.hover.stationary_ticks = int(value)

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    def isVisible(self) -> bool:
        return bool(self._host.isVisible())

    def hide(self) -> None:
        """立即隐藏（不播淡出动画），供关机清理路径调用。"""
        self._host.stop_auto_hide()
        self._host.stop_animation()
        self._visible = False
        self._control.visible = False
        self._host.hide()

    def update(self) -> None:
        """请求重绘；真实窗口在宿主手上。"""
        self._host.update()

    def close(self) -> None:
        """关闭并释放后端窗口（关机清理及单例回收路径）。"""
        self._host.cleanup()

    # ==================================================================
    # Tick 驱动的悬停检测
    # ==================================================================

    def _on_tick(self, event: Event) -> None:
        current = pointer_cursor()
        action = self._control.on_tick(current)
        if action == TOOLTIP_HIDE:
            # 鼠标移动 → 重置计数，隐藏面板
            self._last_pos = current
            if self._visible:
                self._hide()
        elif action == TOOLTIP_SHOW:
            self._last_pos = current
            desc = self._find_description(current)
            if desc:
                self._show(desc, current)

    # ==================================================================
    # 查找说明字段
    # ==================================================================

    #: 这些窗口的说明书只在它们真正激活时才显示（避免背景面板投影）。
    _RESTRICTED_DESCRIPTION_WINDOWS = ("AISettingsPanel", "WorkbenchWindow")

    def _find_description(self, global_pos) -> str:
        """交给后端宿主做 Qt 命中测试，取光标下控件声明的说明。

        命中策略（``widgetAt`` → ``parent()`` 链 → 顶层窗口兜底）与限制规则都在后端宿主里；
        控件只声明"哪些窗口算受限面板"这个产品策略。
        """
        return self._host.description_at(
            global_pos,
            restricted_names=self._RESTRICTED_DESCRIPTION_WINDOWS,
        )

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def _show(self, text: str, cursor) -> None:
        self._current_text = text
        screen = get_screen_geometry_for_point(point=cursor, fallback_widget=self._host)
        placement = self._control.show(text, cursor, screen)
        self._apply_visual_size()
        self._host.move_to(placement.x, placement.y)
        self._host.show()
        self._visible = True
        self._host.start_auto_hide()
        self._animate(1.0)

    def _hide(self) -> None:
        self._host.stop_auto_hide()
        self._visible = False
        self._animate(0.0)

    def hide_now(self, *, reset_hover: bool = True) -> None:
        """立即隐藏提示框，并可选重置悬停计时状态。"""
        self._host.stop_auto_hide()
        self._host.stop_animation()
        self._visible = False
        self._host.hide()
        self._host.set_opacity(0.0)
        if reset_hover:
            self._control.hover.reset(pointer_cursor())
            self._last_pos = pointer_cursor()

    def _animate(self, target: float) -> None:
        # 淡出的判定取自控件自己的可见状态（``_show`` 先置一、``_hide`` 先置零），
        # 不能靠"目标小于当前透明度"推断：淡入还没播完时它们可能相等。
        # 宿主只有在淡出真正播完后才隐藏窗口，否则会留在屏幕上继续拦鼠标。
        self._host.fade_to(
            self._control.scaled_opacity(target * _tooltip_target_opacity()),
            duration_ms=UI['ui_fade_duration'],
            fade_out=not self._visible,
        )

    def _on_fade_out_finished(self) -> None:
        """淡出完成后隐藏窗口，避免占用 z-order。"""
        if not self._visible:
            self._host.hide()

    # ==================================================================
    # 布局计算
    # ==================================================================

    def _apply_visual_size(self) -> None:
        """依据文本内容重新计算面板尺寸。"""
        size = self._control.logical_size()
        self._host.apply_size(int(size.width), int(size.height))

    def _reposition(self, cursor) -> None:
        """将面板放在光标右侧；超出屏幕右/下边界时自动镜像。"""
        screen = get_screen_geometry_for_point(point=cursor, fallback_widget=self._host)
        placement = self._control.place(cursor, screen)
        self._host.move_to(placement.x, placement.y)

    def _wrap_text(self, text: str) -> list[str]:
        """按 _MAX_TEXT_W 像素宽度对文本进行自动换行。"""
        return self._control.wrapped_lines(text)

    def _build_visual(self, text: str | None = None):
        """Resolve the shared tooltip visual for the current text."""
        return self._control.build_visual(text)

    # ==================================================================
    # 绘制
    # ==================================================================

    def _paint_batch(self):
        if not self._current_text:
            return None
        return self._control.build_visual().batch


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
            _instance._ec.unsubscribe(EventType.TICK, _instance._on_tick)
            _instance._host.cleanup()
        except Exception:
            pass
        _instance = None
