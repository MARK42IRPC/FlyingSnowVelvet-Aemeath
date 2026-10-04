"""控件描述层：产品控件的状态与绘制数据，不含控件工具包事实。

产品控件此前同时是"状态机"和"QWidget"：事件订阅、消息队列、淡入淡出、命中判定、
窗口标志与透明度动画混在同一个类里。本模块抽出与工具包无关的那一半，让控件描述
只回答"我现在是什么状态、该画什么、画在哪、多透明"。

- ``BubbleInfo`` / ``BubbleControl``：气泡的可见性、当前消息、待显示队列、
  最小/最大显示 tick 判定、锚点与位置解算、透明度目标、绘制批次。
- ``PointerEvent`` / ``PointerClick``：中立指针事件与它翻译出的产品意图。
- ``AnchorPlacement`` / ``PlacementSpec``：排布解算结果与声明；两者都定义在
  ``visuals/layout.py``，此处重新导出以保持既有调用面。
- ``TooltipHoverState`` / ``TooltipControl``：说明书面板的悬停计数、文本排版、
  尺寸与位置解算；指针位置由调用方喂进来，动作码由它给出。

本模块是后端中立的：不 import PyQt5，也不 import 任何 ``backends/*``。真实窗口、
透明度动画、剪贴板与 z-order 由后端窗口宿主持有（Qt 见
``lib/core/render/backends/qt/widgets/control_host.py``）。跨后端对齐的事实源是绘制批次：
``build_bubble_visual`` 在两个后端上产出同一份批次，描述层只决定"何时、何地、多透明"
把它交出去。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.core.render.visuals.application_visuals import (
    BubbleVisualDescription,
    CommandHintVisualDescription,
    build_bubble_visual,
    build_command_hint_visual,
    build_tooltip_visual,
    resolve_bubble_geometry,
)
from lib.core.render.visuals.anchors import get_anchor_point
from lib.core.render.visuals.layout import AnchorPlacement, PlacementSpec
from lib.core.render.visuals.media_panel_visuals import (
    SEARCH_RESULT_PAGE_SIZE,
    SLIDER_TICK_COUNT,
    build_progress_panel_visual,
    build_search_result_panel_visual,
    build_slider_visual,
    search_result_panel_size,
    slider_ratio_at,
    snap_slider_ratio,
)
from lib.core.render.visuals.speaker_band_visuals import (
    band_hit_test,
    band_ratio_at,
    build_band_slider_visual,
)
from lib.core.render.visuals.screen import clamp_rect_position
from lib.core.render.visuals.types import Point, Rect, Size

if TYPE_CHECKING:
    from lib.core.render.backends.base import TextMetrics

__all__ = [
    "HOVER_HIDE",
    "HOVER_NONE",
    "HOVER_SHOW",
    "AnchorPlacement",
    "MediaProgressControl",
    "MicSttControl",
    "RectActionButtonControl",
    "RectSliderControl",
    "BandSliderControl",
    "SearchResultListControl",
    "CommandHintControl",
    "BubbleControl",
    "BubbleInfo",
    "PointerClick",
    "PointerEvent",
    "TOOLTIP_HIDE",
    "TOOLTIP_IDLE",
    "TOOLTIP_SHOW",
    "TooltipControl",
    "TooltipHoverState",
    "scaled_opacity",
]

BUTTON_LEFT = "left"
BUTTON_RIGHT = "right"
BUTTON_MIDDLE = "middle"
BUTTON_NONE = "none"

#: ``BubbleControl.on_tick`` 的动作码，控件宿主据此决定做哪件窗口操作。
TICK_NONE = "none"
TICK_SHOW_NEXT = "show_next"
TICK_REPLACE_NEXT = "replace_next"
TICK_HIDE = "hide"

#: 鼠标按钮到点击粒子 ID 的映射（与 `_particle_helper` 同一份事实）。
BUTTON_PARTICLES = {BUTTON_LEFT: "click", BUTTON_RIGHT: "pink_click"}

#: 悬停距离判定的动作码（语音指示器）。
HOVER_NONE = "none"
HOVER_SHOW = "show"
HOVER_HIDE = "hide"

#: ``TooltipControl.on_tick`` 的动作码。
TOOLTIP_IDLE = "idle"
TOOLTIP_SHOW = "show"
TOOLTIP_HIDE = "hide"


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """一次指针按压的中立描述：局部坐标用于命中，屏幕坐标用于粒子。"""

    button: str = BUTTON_NONE
    local: Point = Point()
    screen: Point = Point()


@dataclass(frozen=True, slots=True)
class PointerClick:
    """指针事件翻译出的产品意图。"""

    copy_text: str | None = None
    hide: bool = False
    particle_id: str | None = None

    @property
    def consumed(self) -> bool:
        return self.copy_text is not None or self.hide


class BubbleInfo:
    """一条待显示/正在显示的气泡消息。"""

    def __init__(
        self,
        text: str,
        min_ticks: int,
        max_ticks: int,
        align: str = "center",
        source: str = "",
        task_id: str = "",
        kind: str = "",
    ) -> None:
        self.text = text
        self.min_ticks = min_ticks
        self.max_ticks = max_ticks
        self.elapsed_ticks = 0
        self.align = align  # 'left' | 'center'
        self.source = str(source or "")
        self.task_id = str(task_id or "")
        self.kind = str(kind or "")


class BubbleControl:
    """气泡框的后端中立状态与绘制描述。"""

    def __init__(
        self,
        metrics: "TextMetrics",
        *,
        max_width: float,
        padding: float,
        border_width: float,
        fade_duration_ms: int,
        paint_layer: int,
        opacity_scale=1.0,
        placeholder_size: tuple[float, float] = (100.0, 40.0),
        ui_id: str = "bubble",
        target_ui_id: str = "pet_window",
        target_anchor_id: str = "top",
        self_anchor_id: str = "bottom",
    ) -> None:
        self.metrics = metrics
        self.max_width = float(max_width)
        self.padding = float(padding)
        self.border_width = float(border_width)
        self.fade_duration_ms = int(fade_duration_ms)
        self.paint_layer = int(paint_layer)
        self.placeholder_size = (float(placeholder_size[0]), float(placeholder_size[1]))
        self._opacity_scale = opacity_scale

        self.ui_id = str(ui_id)
        self.target_ui_id = str(target_ui_id)
        self.target_anchor_id = str(target_anchor_id)
        self.self_anchor_id = str(self_anchor_id)

        self.visible = False
        self.fading_out = False
        self.anchor_available = False
        self.clickthrough = False
        self.current: BubbleInfo | None = None
        self.pending_queue: list[tuple] = []
        self.anchor_point: Point | None = None
        self.offset_x = 0
        self.offset_y = 0
        self.visual: BubbleVisualDescription | None = None

    # ── 绘制数据 ───────────────────────────────────────────────────
    def build_visual(self, text: str, align: str = "center") -> BubbleVisualDescription:
        """解析消息的排版、尺寸与绘制批次（两个后端共用同一份）。"""
        return build_bubble_visual(
            text,
            self.metrics,
            max_width=self.max_width,
            padding=self.padding,
            border_width=self.border_width,
            align=align,
            layer=self.paint_layer,
        )

    def set_message(self, text: str, align: str = "center") -> BubbleVisualDescription:
        """刷新当前消息的绘制批次。"""
        self.visual = self.build_visual(text, align)
        return self.visual

    def text_size(self, text: str) -> tuple[int, int]:
        visual = self.build_visual(text)
        return int(visual.size.width), int(visual.size.height)

    # ── 尺寸、锚点与位置 ───────────────────────────────────────────
    def logical_size(self) -> Size:
        if self.visual is None:
            return Size(*self.placeholder_size)
        return Size(float(self.visual.size.width), float(self.visual.size.height))

    def anchor_local(self, anchor_id: str) -> Point:
        """锚点在窗口本地坐标系中的位置（核心 ``Point``，不是 ``QPoint``）。"""
        size = self.logical_size()
        table = {
            "top": Point(size.width // 2, 0.0),
            "bottom": Point(size.width // 2, size.height),
            "left": Point(0.0, size.height // 2),
            "right": Point(size.width, size.height // 2),
            "top_left": Point(0.0, 0.0),
            "top_right": Point(size.width, 0.0),
            "bottom_left": Point(0.0, size.height),
            "bottom_right": Point(size.width, size.height),
            "center": Point(size.width // 2, size.height // 2),
        }
        return table.get(anchor_id, table["center"])

    def anchor_global(self, anchor_id: str, host_rect: Rect | None = None) -> Point:
        """任意锚点在屏幕坐标系中的位置，用于回应锚点请求。

        以宿主窗口的实际矩形为准（夹取可能把窗口挪回屏幕内，不能只看目标锚点）。
        拿不到宿主几何时退回"自身下锚点钉在 ``anchor_point`` 上"的解算。
        """
        local = self.anchor_local(anchor_id)
        if host_rect is not None:
            return Point(host_rect.x + local.x, host_rect.y + local.y)
        if self.anchor_point is None:
            return Point()
        base = self.anchor_local(self.self_anchor_id)
        return Point(
            self.anchor_point.x + local.x - base.x,
            self.anchor_point.y + local.y - base.y,
        )

    def set_anchor_point(self, value) -> None:
        """设置目标锚点（屏幕坐标）；无效输入被忽略。"""
        if value is None:
            return
        if isinstance(value, Point):
            self.anchor_point = value
            return
        x = getattr(value, "x", None)
        y = getattr(value, "y", None)
        if callable(x):
            x = x()
        if callable(y):
            y = y()
        try:
            self.anchor_point = Point(float(x), float(y))
        except (TypeError, ValueError):
            return

    def placement(self, screen: Rect) -> AnchorPlacement | None:
        """把自身下锚点对到目标上锚点，并夹取回屏幕内。"""
        if self.anchor_point is None:
            return None
        size = self.logical_size()
        geometry = resolve_bubble_geometry(
            self.anchor_point,
            Size(size.width, size.height),
            screen,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
        )
        return AnchorPlacement(int(geometry.x), int(geometry.y), screen)

    # ── 透明度 ─────────────────────────────────────────────────────
    def scaled_opacity(self, target: float) -> float:
        """目标透明度乘上全局 UI 控件透明度设置后夹取到 ``[0, 1]``。"""
        return scaled_opacity(self._opacity_scale, target)

    # ── 队列与显示判定 ─────────────────────────────────────────────
    def add(self, text, min_ticks, max_ticks, align, particle, force_replace, *, source, task_id, kind) -> str:
        """决定新消息是替换还是排队；``force_replace`` 时清空队列强插。"""
        if force_replace:
            self.pending_queue.clear()
            return "replace"
        if self.current is None:
            return "replace"
        if self.current.elapsed_ticks >= self.current.min_ticks:
            return "replace"
        self.enqueue(text, min_ticks, max_ticks, align, particle, source, task_id, kind)
        return "enqueue"

    def enqueue(self, text, min_ticks, max_ticks, align, particle, source, task_id, kind) -> None:
        self.pending_queue.append((
            text, min_ticks, max_ticks, align, particle,
            str(source or ""), str(task_id or ""), str(kind or ""),
        ))

    def pop_next(self):
        if not self.pending_queue:
            return None
        return self.pending_queue.pop(0)

    def clear_queue(self) -> None:
        self.pending_queue.clear()

    def on_tick(self) -> str:
        """推进 tick 状态机，返回 ``TICK_*`` 动作码。"""
        if self.current is None:
            return TICK_SHOW_NEXT if self.pending_queue else TICK_NONE
        if self.fading_out:
            return TICK_NONE
        self.current.elapsed_ticks += 1
        if self.current.elapsed_ticks >= self.current.max_ticks:
            return TICK_HIDE
        if self.current.elapsed_ticks >= self.current.min_ticks and self.pending_queue:
            return TICK_REPLACE_NEXT
        return TICK_NONE

    # ── 指针 ───────────────────────────────────────────────────────
    def click_intent(self, event: PointerEvent, *, particle: bool = True) -> PointerClick:
        """左键关闭、右键复制并关闭；两种都可发射点击粒子。"""
        if event.button == BUTTON_LEFT:
            return PointerClick(hide=True, particle_id="click" if particle else None)
        if event.button == BUTTON_RIGHT:
            text = self.current.text if self.current is not None else None
            return PointerClick(copy_text=text, hide=True, particle_id="pink_click" if particle else None)
        return PointerClick()

    # ── 按元数据撤销 ───────────────────────────────────────────────
    @staticmethod
    def metadata_matches(source, task_id, kind, *, item_source, item_task_id, item_kind) -> bool:
        return (
            (not source or item_source == str(source))
            and (not task_id or item_task_id == str(task_id))
            and (not kind or item_kind == str(kind))
        )

    def drop_matching(self, *, source: str = "", task_id: str = "", kind: str = "") -> bool:
        """按来源/任务/类别撤销队列与当前消息；当前消息命中时返回 True。"""
        self.pending_queue = [
            item for item in self.pending_queue
            if not self.metadata_matches(
                source, task_id, kind,
                item_source=item[5], item_task_id=item[6], item_kind=item[7],
            )
        ]
        current = self.current
        if current is None:
            return False
        return self.metadata_matches(
            source, task_id, kind,
            item_source=current.source, item_task_id=current.task_id, item_kind=current.kind,
        )


def scaled_opacity(opacity_scale, target: float) -> float:
    """把目标透明度乘上全局 UI 控件透明度设置后夹取到 ``[0, 1]``。

    ``opacity_scale`` 可以是数值，也可以是无参可调用对象（设置需要每帧实时读取）。
    """
    raw = opacity_scale() if callable(opacity_scale) else opacity_scale
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        scale = 1.0
    base = max(0.0, min(1.0, float(target)))
    return max(0.0, min(1.0, base * max(0.0, min(1.0, scale))))


def _to_point(value):
    """把 ``Point`` / ``QPoint`` / ``(x, y)`` 统一成核心 ``Point``；无效输入返回 ``None``。"""
    if value is None:
        return None
    if isinstance(value, Point):
        return value
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if callable(x):
        x = x()
    if callable(y):
        y = y()
    if x is None or y is None:
        try:
            x, y = value[0], value[1]
        except (TypeError, KeyError, IndexError):
            return None
    try:
        return Point(float(x), float(y))
    except (TypeError, ValueError):
        return None


class TooltipHoverState:
    """说明书（悬停提示）的悬停计时状态。

    光标位置是屏幕事实，但"静止够久了没有"是产品判定：把位置喂进来，由本对象
    决定这一刻该做什么。宿主与后端只负责把动作码变成真实窗口操作。
    """

    def __init__(self, hover_ticks: int, position=None) -> None:
        self.hover_ticks = max(0, int(hover_ticks))
        self.stationary_ticks = 0
        self.last_position: Point | None = _to_point(position)

    def advance(self, position) -> str:
        """喂入本 tick 的指针位置，返回 ``TOOLTIP_*`` 动作码。"""
        current = _to_point(position)
        if current is None:
            return TOOLTIP_IDLE
        if self.last_position is None:
            self.last_position = current
            return TOOLTIP_IDLE
        if current != self.last_position:
            self.last_position = current
            self.stationary_ticks = 0
            return TOOLTIP_HIDE
        if self.stationary_ticks < self.hover_ticks:
            self.stationary_ticks += 1
            if self.stationary_ticks == self.hover_ticks:
                return TOOLTIP_SHOW
        return TOOLTIP_IDLE

    def reset(self, position=None) -> None:
        """重置静止计数；给了位置就同时把"上次位置"挪过去。"""
        self.stationary_ticks = 0
        if position is not None:
            self.last_position = _to_point(position)


class TooltipControl:
    """说明书面板的后端中立状态与绘制描述。

    排版、换行、尺寸与绘制批次走的是共享 presenter（``build_tooltip_visual``），
    Qt 与 DirectX 两个后端产出同一份批次；这里只决定"显示什么文本、停在哪、多透明"。
    """

    def __init__(
        self,
        metrics: "TextMetrics",
        *,
        max_text_width: float,
        padding_x: float,
        padding_y: float,
        border_width: float,
        cursor_gap: float,
        min_text_width: float = 0.0,
        paint_layer: int,
        opacity_scale=1.0,
        hover_ticks: int = 20,
        auto_hide_ms: int = 5000,
        initial_position=None,
    ) -> None:
        self.metrics = metrics
        self.max_text_width = float(max_text_width)
        self.padding_x = float(padding_x)
        self.padding_y = float(padding_y)
        self.border_width = float(border_width)
        self.cursor_gap = float(cursor_gap)
        self.min_text_width = float(min_text_width)
        self.paint_layer = int(paint_layer)
        self.auto_hide_ms = max(0, int(auto_hide_ms))
        self._opacity_scale = opacity_scale

        self.visible = False
        self.text = ""
        self.hover = TooltipHoverState(hover_ticks, initial_position)
        self.visual: "BubbleVisualDescription | None" = None
        #: 最近一次解算出的面板左上角与所在屏幕；宿主据此移动窗口。
        self.placement: "AnchorPlacement | None" = None

    # ── 绘制数据 ───────────────────────────────────────────────────
    def build_visual(self, text: str | None = None) -> "BubbleVisualDescription":
        """解析文本的换行、尺寸与绘制批次。"""
        return build_tooltip_visual(
            self.text if text is None else text,
            self.metrics,
            max_text_width=self.max_text_width,
            padding_x=self.padding_x,
            padding_y=self.padding_y,
            border_width=self.border_width,
            opacity=1.0,
            min_text_width=self.min_text_width,
            layer=self.paint_layer,
        )

    def set_text(self, text: str) -> "BubbleVisualDescription":
        """刷新当前文本的绘制批次。"""
        self.text = str(text or "")
        self.visual = self.build_visual()
        return self.visual

    def wrapped_lines(self, text: str | None = None) -> list[str]:
        return list(self.build_visual(text).lines)

    def logical_size(self) -> Size:
        visual = self.visual if self.visual is not None else self.build_visual()
        return Size(float(visual.size.width), float(visual.size.height))

    # ── 坐标与位置 ─────────────────────────────────────────────────
    @staticmethod
    def _cursor_core(cursor) -> Point | None:
        if cursor is None:
            return None
        if isinstance(cursor, Point):
            return cursor
        point = _to_point(cursor)
        if point is None:
            return None
        return Point(int(point.x), int(point.y))

    def place(self, cursor, screen: Rect) -> "AnchorPlacement":
        """把面板放在光标右侧；越界时镜像到左侧，并夹取回屏幕内。

        ``placement`` 同时保留在描述对象上，供"没有返回值的调用方"（例如刷新重排）
        读取；返回值只是同一份结果的显式副本。
        """
        anchor = self._cursor_core(cursor) or Point()
        size = self.logical_size()
        x = anchor.x + self.cursor_gap
        y = anchor.y
        if x + size.width > screen.x + screen.width:
            x = anchor.x - size.width - self.cursor_gap
        clamped_x, clamped_y, _ = clamp_rect_position(
            int(x), int(y), int(size.width), int(size.height), screen
        )
        self.placement = AnchorPlacement(clamped_x, clamped_y, screen)
        return self.placement

    # ── 透明度 ─────────────────────────────────────────────────────
    def scaled_opacity(self, target: float) -> float:
        """目标透明度乘上全局 UI 控件透明度设置后夹取到 ``[0, 1]``。"""
        return scaled_opacity(self._opacity_scale, target)

    # ── 悬停状态机 ─────────────────────────────────────────────────
    def on_tick(self, cursor) -> str:
        """推进悬停判定，返回 ``TOOLTIP_*`` 动作码。

        ``TOOLTIP_SHOW`` 只在刚刚达到静止阈值那一 tick 给出（防止每帧重复触发）；
        面板已经隐藏时不再重复要求隐藏。
        """
        action = self.hover.advance(cursor)
        if action == TOOLTIP_HIDE and not self.visible:
            return TOOLTIP_IDLE
        return action

    def show(self, text: str, cursor, screen: Rect) -> "AnchorPlacement":
        self.set_text(text)
        self.place(cursor, screen)
        self.visible = True
        return self.placement

    def hide(self, cursor=None, *, reset_hover: bool = False) -> None:
        self.visible = False
        if reset_hover:
            self.hover.reset(cursor)


def ui_opacity_scale() -> float:
    """全局 UI 控件透明度设置（惰性读取，避免 import 期固化配置）。"""
    from lib.core.anchor_utils import apply_ui_opacity

    return apply_ui_opacity(1.0)


_POINTING_HAND_CLICK = PointerClick(particle_id="click")


class MicSttControl:
    """语音识别状态指示器的后端中立状态。"""

    def __init__(
        self,
        *,
        size: float,
        hover_radius: float,
        hide_delay: float,
        paint_layer: int,
        opacity_scale=1.0,
    ) -> None:
        self.size = float(size)
        self.hover_radius = float(hover_radius)
        self.hide_delay = float(hide_delay)
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.visible = False
        self.listening = False
        self.speech_active = False
        self.last_pointer_inside_ts = 0.0

    def mark_pointer_inside(self, position=None, *, now: float | None = None) -> None:
        """记录"指针此刻在指示器附近"；忽略 ``position``，调用方已判定在外。"""
        self.last_pointer_inside_ts = time.monotonic() if now is None else float(now)

    def update_hover(self, pointer, rect, *, now: float | None = None) -> str:
        """指针在半径内是"靠近"，离开超过 ``hide_delay`` 才收起。

        ``rect`` 是指示器当前的屏幕矩形，中心点由它算；``pointer`` 是核心 ``Point``。
        返回 ``HOVER_*`` 动作码，控件据此决定显示或隐藏。
        """
        if pointer is None or rect is None:
            return HOVER_NONE
        pointer_x, pointer_y = getattr(pointer, "x", None), getattr(pointer, "y", None)
        if callable(pointer_x):
            pointer_x = pointer_x()
        if callable(pointer_y):
            pointer_y = pointer_y()
        try:
            dx = float(pointer_x) - (float(rect.x) + float(rect.width) / 2.0)
            dy = float(pointer_y) - (float(rect.y) + float(rect.height) / 2.0)
        except (TypeError, ValueError):
            return HOVER_NONE

        moment = time.monotonic() if now is None else float(now)
        if dx * dx + dy * dy <= self.hover_radius ** 2:
            self.last_pointer_inside_ts = moment
            return HOVER_SHOW if self.listening else HOVER_NONE
        if self.visible and (moment - self.last_pointer_inside_ts) >= self.hide_delay:
            return HOVER_HIDE
        return HOVER_NONE

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)

    def click_intent(self, event: PointerEvent) -> PointerClick:
        """左键点击 → 要求停止语音识别（点击粒子同步发出）。"""
        if event.button == BUTTON_LEFT:
            return _POINTING_HAND_CLICK
        return PointerClick()


class RectActionButtonControl:
    """定长矩形动作按钮的后端中立状态（穿透 / 关闭 / 缩放 / 模式切换等共用）。

    这些按钮形状一致：固定宽高、淡入淡出、悬停高亮、锚点跟随、点击发事件。差异只有
    "文字、锚点指向、点击产物"，因此状态与解算收在这里，各控件只保留自己的点击语义。
    """

    def __init__(
        self,
        *,
        width: float,
        height: float,
        text: str = "",
        fade_duration_ms: int = 200,
        paint_layer: int = 0,
        opacity_scale=1.0,
        hovered: bool = False,
    ) -> None:
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.text = str(text or "")
        self.fade_duration_ms = int(fade_duration_ms)
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.hovered = bool(hovered)
        self.visible = False
        self.clickthrough = False
        self.anchor_point: Point | None = None
        self.anchor_available = True

    # ── 绘制数据 ───────────────────────────────────────────────────
    def logical_size(self) -> Size:
        return Size(self.width, self.height)

    def visual_rect(self) -> Rect:
        return Rect(0.0, 0.0, self.width, self.height)

    def label(self) -> str:
        """按钮文字；子类可覆盖成动态文本（模式名、'+' / '-' 等）。"""
        return self.text

    # ── 锚点解算 ───────────────────────────────────────────────────
    def anchor_local(self, anchor_id: str) -> Point:
        return get_anchor_point(self.visual_rect(), anchor_id)

    def placement_spec(
        self,
        *,
        target_anchor_id: str = "top_left",
        self_anchor_id: str = "bottom_left",
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> PlacementSpec:
        """本按钮默认的落位声明；解算统一走 ``visuals/layout.py``。"""
        return PlacementSpec(
            target_anchor_id=target_anchor_id,
            self_anchor_id=self_anchor_id,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def anchored_top_left(
        self,
        target_rect: Rect,
        *,
        target_anchor_id: str = "top_left",
        self_anchor_id: str = "bottom_left",
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> Point:
        """把自身锚点对到目标矩形上的锚点，返回窗口左上角（未夹取）。"""
        return self.placement_spec(
            target_anchor_id=target_anchor_id,
            self_anchor_id=self_anchor_id,
            offset_x=offset_x,
            offset_y=offset_y,
        ).resolve_point(self.logical_size(), target_rect)

    def placement(
        self,
        target_rect: Rect,
        screen: Rect,
        *,
        target_anchor_id: str = "top_left",
        self_anchor_id: str = "bottom_left",
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> AnchorPlacement:
        return self.placement_spec(
            target_anchor_id=target_anchor_id,
            self_anchor_id=self_anchor_id,
            offset_x=offset_x,
            offset_y=offset_y,
        ).resolve_placement(self.logical_size(), target_rect, screen)

    def set_anchor_point(self, value) -> None:
        if value is None:
            self.anchor_point = None
            return
        point = _to_point(value)
        if point is not None:
            self.anchor_point = point

    def anchor_global(self, anchor_id: str, host_rect: Rect | None = None) -> Point:
        if host_rect is not None:
            local = self.anchor_local(anchor_id)
            return Point(host_rect.x + local.x, host_rect.y + local.y)
        if self.anchor_point is None:
            return Point()
        return self.anchor_point

    # ── 透明度与指针 ───────────────────────────────────────────────
    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)

    @staticmethod
    def click_particle_id(event: PointerEvent) -> str | None:
        """左键 / 右键各自的点击粒子；其它按键不发射。"""
        return BUTTON_PARTICLES.get(event.button)


class MediaProgressControl:
    """播放进度条的后端中立状态与解算。

    进度、剩余时长、拖动与"拖动时反推剩余时间"都是产品算术；滑条区域与绘制批次
    来自共享 presenter（``build_progress_panel_visual``），x ↔ 进度的换算直接读
    ``visual.slider_rect``，不把版面参数抄第二份。
    """

    def __init__(
        self,
        metrics,
        *,
        width: float,
        height: float,
        gap: float,
        paint_layer: int,
        opacity_scale=1.0,
    ) -> None:
        self.metrics = metrics
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.gap = float(gap)
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.visible = False
        self.progress = 0.0
        self.remaining = 0
        self.playing = False
        self.paused = False
        self.dragging = False
        self.drag_progress = 0.0
        self.tick_counter = 0

    # ── 绘制数据 ───────────────────────────────────────────────────
    def logical_size(self) -> Size:
        return Size(self.width, self.height)

    def build_visual(self):
        return build_progress_panel_visual(
            progress=self.render_progress(),
            time_text=self.time_text(),
            metrics=self.metrics,
            layer=self.paint_layer,
        )

    def render_progress(self) -> float:
        return self.drag_progress if self.dragging else self.progress

    def remaining_seconds(self) -> int:
        """拖动时按当前进度反推剩余时间，否则用音乐模块给的剩余时长。"""
        if not self.dragging:
            return self.remaining
        if 0.0 < self.progress < 1.0:
            total_time = self.remaining / (1.0 - self.progress)
            return int(total_time * (1.0 - self.drag_progress))
        return self.remaining

    def time_text(self) -> str:
        remaining = self.remaining_seconds()
        return f"{remaining // 60}:{remaining % 60:02d}"

    # ── 位置 ───────────────────────────────────────────────────────
    def placement_spec(self) -> PlacementSpec:
        """左下锚点对齐播放列表的左上锚点；``gap`` 用负 Y 偏移表达。"""
        return PlacementSpec(
            target_anchor_id="top_left",
            self_anchor_id="bottom_left",
            offset_y=-float(self.gap),
        )

    def placement(self, playlist_rect: Rect, screen: Rect) -> AnchorPlacement:
        """左下锚点对齐播放列表的左上锚点：进度条落在播放列表正上方。"""
        return self.placement_spec().resolve_placement(
            self.logical_size(), playlist_rect, screen
        )

    # ── 拖动：x <-> 进度 ───────────────────────────────────────────
    def slider_rect(self) -> Rect:
        return self.build_visual().slider_rect

    def progress_to_x(self, progress: float) -> int:
        slider = self.slider_rect()
        return int(slider.x + float(progress) * float(slider.width))

    def x_to_progress(self, x: float) -> float:
        slider = self.slider_rect()
        left = float(slider.x)
        width = float(slider.width) or 1.0
        clamped = max(left, min(float(x), left + width))
        return max(0.0, min(1.0, (clamped - left) / width))

    def begin_drag(self, local_x: float) -> None:
        self.dragging = True
        self.drag_progress = self.x_to_progress(local_x)

    def update_drag(self, local_x: float) -> None:
        if self.dragging:
            self.drag_progress = self.x_to_progress(local_x)

    def end_drag(self) -> float:
        """结束拖动并提交进度，返回要发布出去的 seek 进度。"""
        self.dragging = False
        self.progress = self.drag_progress
        return self.progress

    # ── 状态与节奏 ─────────────────────────────────────────────────
    def apply_progress(self, progress: float, remaining: int) -> None:
        self.progress = progress
        self.remaining = remaining

    def reset_progress(self) -> None:
        self.progress = 0.0
        self.remaining = 0

    def advance_tick(self, every: int = 20) -> bool:
        """累计 tick；到达 ``every`` 的整数倍时返回 True，表示该请求一次进度。"""
        if not self.visible or self.dragging:
            return False
        self.tick_counter += 1
        if self.tick_counter < every:
            return False
        self.tick_counter = 0
        return True

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)


class RectSliderControl:
    """共享水平滑条的后端中立状态（音量滑条的描述层）。

    形状、刻度与手柄来自共享 presenter（``build_slider_visual``），比例<->横坐标换算走
    ``media_panel_visuals`` 的同一份算术。控件只保留"当前比例、是否在拖动"，真实窗口
    与拖动捕获由后端窗口宿主持有。
    """

    def __init__(
        self,
        *,
        width: float,
        height: float,
        ticks: int = SLIDER_TICK_COUNT,
        paint_layer: int = 0,
        opacity_scale=1.0,
    ) -> None:
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.ticks = max(1, int(ticks))
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.visible = False
        self.ratio = 0.0
        self.dragging = False

    # ── 绘制数据 ───────────────────────────────────────────────────
    def logical_size(self) -> Size:
        return Size(self.width, self.height)

    def build_visual(self):
        return build_slider_visual(
            ratio=self.ratio,
            width=int(round(self.width)),
            height=int(round(self.height)),
            ticks=self.ticks,
            layer=self.paint_layer,
        )

    def track_rect(self) -> Rect:
        return build_slider_visual(
            ratio=self.ratio,
            width=int(round(self.width)),
            height=int(round(self.height)),
            ticks=self.ticks,
            layer=self.paint_layer,
        ).track_rect

    # ── 拖动：x <-> 比例 ───────────────────────────────────────────
    def snap(self, ratio: float) -> float:
        return snap_slider_ratio(ratio, self.ticks)

    def ratio_from_x(self, x: float) -> float:
        return slider_ratio_at(self.track_rect(), x)

    def set_ratio(self, ratio: float) -> tuple[float, bool]:
        """吸附到刻度并记录；返回 ``(新比例, 是否变化)``。"""
        snapped = self.snap(ratio)
        changed = abs(snapped - self.ratio) > 1e-9
        self.ratio = snapped
        return snapped, changed

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)


class BandSliderControl:
    """音响响应频段竖向滑条的后端中立状态。

    频段读数、拖动命中与"块跟随指针"的算术都在这里；频段本身按世界对象实例存在
    ``lib.core.speaker_band`` 里，通过 ``speaker`` 句柄读写。真实窗口与拖动捕获由后端
    窗口宿主持有。
    """

    def __init__(
        self,
        *,
        width: float,
        height: float,
        band: tuple[float, float],
        paint_layer: int = 0,
        opacity_scale=1.0,
    ) -> None:
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.band = band
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.visible = False
        self.dragging = ""
        self.speaker = None

    def logical_size(self) -> Size:
        return Size(self.width, self.height)

    def build_visual(self):
        return build_band_slider_visual(
            band=self.band,
            width=int(round(self.width)),
            height=int(round(self.height)),
            layer=self.paint_layer,
        )

    def hit_test(self, x: float, y: float) -> str:
        visual = self.build_visual()
        return band_hit_test(visual.track_rect, visual.center_rect, x, y)

    def ratio_at(self, y: float) -> float:
        return band_ratio_at(self.build_visual().track_rect, y)

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)



class SearchResultListControl:
    """音响搜索结果列表的后端中立状态与绘制描述。

    - 列表数据、翻页、选中行、搜索中标记；
    - 窗口尺寸来自 ``search_result_panel_size``（按最宽混排行自适应）；
    - 悬停/点击的行由 ``row_at_y`` 用共享 ``visual.row_rects`` 反查，不再自己手算
      ``(y - border) // row_height``；
    - 绘制批次由 ``build_visual`` 产出，宿主只执行它。
    """

    def __init__(
        self,
        metrics: "TextMetrics",
        *,
        page_size: int = SEARCH_RESULT_PAGE_SIZE,
        paint_layer: int = 0,
        opacity_scale=1.0,
    ) -> None:
        self.metrics = metrics
        self.page_size = max(1, int(page_size))
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.items: list[tuple[object, str]] = []
        self.page = 0
        self.selected = -1
        self.searching = False
        self.visible = False
        self.width = 1
        self.height = 1

    # ── 数据 ───────────────────────────────────────────────────────
    def page_items(self) -> list[tuple[object, str]]:
        start = self.page * self.page_size
        return self.items[start: start + self.page_size]

    def has_pages(self) -> bool:
        return len(self.items) > self.page_size

    def max_page(self) -> int:
        return max(0, (len(self.items) - 1) // self.page_size)

    def clear(self) -> None:
        self.items = []
        self.selected = -1
        self.page = 0

    def set_items(self, items) -> None:
        self.items = list(items)
        self.selected = 0 if self.items else -1
        self.page = 0

    def navigate(self, direction: int) -> bool:
        if self.searching or not self.items:
            return False
        items = self.page_items()
        new_selected = self.selected + direction
        if 0 <= new_selected < len(items):
            self.selected = new_selected
            return True
        return False

    def turn_page(self, direction: int) -> bool:
        if self.searching or not self.items:
            return False
        max_page = self.max_page()
        if max_page == 0:
            return False
        new_page = self.page + direction
        # 循环翻页：超出范围时跳转到另一端
        if new_page < 0:
            new_page = max_page
        elif new_page > max_page:
            new_page = 0
        self.page = new_page
        self.selected = 0
        return True

    # ── 尺寸与绘制 ─────────────────────────────────────────────────
    def refresh_size(self) -> tuple[int, int]:
        size = search_result_panel_size(
            tuple(self.items),
            self.metrics,
            page=self.page,
            page_size=self.page_size,
            searching=self.searching,
        )
        self.width, self.height = int(size.width), int(size.height)
        return self.width, self.height

    def build_visual(self):
        return build_search_result_panel_visual(
            Size(self.width, self.height),
            tuple(self.items),
            self.metrics,
            page=self.page,
            page_size=self.page_size,
            selected=self.selected,
            searching=self.searching,
            layer=self.paint_layer,
        )

    def row_at_y(self, y: float) -> int:
        """悬停/点击的行号；不在任何一行上时返回 -1。"""
        visual = self.build_visual()
        for index, rect in enumerate(visual.row_rects):
            if rect.y <= y < rect.y + rect.height:
                return index
        return -1

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)


#: ``CommandHintControl.mode`` 的两个取值。
COMMAND_HINT_DEFAULT = "default"
COMMAND_HINT_HASH = "hash"


class CommandHintControl:
    """命令提示框的后端中立状态与绘制描述。

    - ``default`` 模式显示几条静态说明行；``hash`` 模式显示 ``#`` 命令过滤结果；
    - 翻页（循环）、选中行、``Tab`` 补全串都在这里；
    - 尺寸与逐行矩形来自共享 ``build_command_hint_visual``，命中行由 ``row_at_y`` 用
      ``visual.row_rects`` 反查，控件不再自己算行高。
    """

    def __init__(
        self,
        metrics: "TextMetrics",
        *,
        default_items: tuple[str, ...] = (),
        page_size: int,
        paint_layer: int = 0,
        opacity_scale=1.0,
    ) -> None:
        self.metrics = metrics
        self.default_items = tuple(default_items)
        self.page_size = max(1, int(page_size))
        self.paint_layer = int(paint_layer)
        self._opacity_scale = opacity_scale

        self.mode = COMMAND_HINT_DEFAULT
        self.all_items: list = []
        self.selected = -1
        self.page = 0
        self.visible = False
        self.anchor_available = False
        self.visual: CommandHintVisualDescription | None = None
        self.set_default_mode()

    # ── 模式 ───────────────────────────────────────────────────────
    def set_default_mode(self) -> None:
        self.mode = COMMAND_HINT_DEFAULT
        self.all_items = list(self.default_items)
        self.selected = 0 if self.all_items else -1
        self.page = 0

    def set_hash_mode(self, items) -> None:
        self.mode = COMMAND_HINT_HASH
        self.all_items = list(items)
        self.selected = 0 if self.all_items else -1
        self.page = 0

    # ── 分页与选中 ─────────────────────────────────────────────────
    def page_items(self) -> list:
        start = self.page * self.page_size
        return self.all_items[start: start + self.page_size]

    def has_pages(self) -> bool:
        return len(self.all_items) > self.page_size

    def max_page(self) -> int:
        return max(0, (len(self.all_items) - 1) // self.page_size)

    def navigate(self, direction: int) -> bool:
        if self.mode != COMMAND_HINT_HASH:
            return False
        items = self.page_items()
        if not items:
            return False
        new_selected = self.selected + direction
        if 0 <= new_selected < len(items):
            self.selected = new_selected
            return True
        return False

    def turn_page(self, direction: int) -> bool:
        if self.mode != COMMAND_HINT_HASH or not self.all_items:
            return False
        max_page = self.max_page()
        if max_page == 0:
            return False
        new_page = self.page + direction
        # 循环翻页：超出范围时跳转到另一端
        if new_page < 0:
            new_page = max_page
        elif new_page > max_page:
            new_page = 0
        self.page = new_page
        self.selected = 0
        return True

    def completion(self) -> str:
        """当前选中命令的补全串（含 ``#`` 前缀与尾部空格）；无选中时返回空串。"""
        if self.mode != COMMAND_HINT_HASH or self.selected < 0:
            return ""
        items = self.page_items()
        if 0 <= self.selected < len(items):
            return f"#{items[self.selected][0]} "
        return ""

    # ── 尺寸、绘制与命中 ───────────────────────────────────────────
    def build_visual(self) -> CommandHintVisualDescription:
        self.visual = build_command_hint_visual(
            self.mode,
            self.all_items,
            self.selected,
            self.page,
            self.metrics,
            layer=self.paint_layer,
        )
        return self.visual

    def ensure_visual(self) -> CommandHintVisualDescription:
        return self.visual if self.visual is not None else self.build_visual()

    def row_at_y(self, y: float) -> int:
        visual = self.visual
        if visual is None:
            return -1
        for index, rect in enumerate(visual.row_rects):
            if rect.y <= y < rect.y + rect.height:
                return index
        return -1

    def page_indicator_contains(self, y: float) -> bool:
        visual = self.visual
        if visual is None or visual.page_indicator_rect is None:
            return False
        rect = visual.page_indicator_rect
        return rect.y <= y < rect.y + rect.height

    def scaled_opacity(self, target: float) -> float:
        return scaled_opacity(self._opacity_scale, target)
