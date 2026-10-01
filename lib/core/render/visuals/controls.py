"""控件描述层：产品控件的状态与绘制数据，不含控件工具包事实。

产品控件此前同时是"状态机"和"QWidget"：事件订阅、消息队列、淡入淡出、命中判定、
窗口标志与透明度动画混在同一个类里。本模块抽出与工具包无关的那一半，让控件描述
只回答"我现在是什么状态、该画什么、画在哪、多透明"。

- ``BubbleInfo`` / ``BubbleControl``：气泡的可见性、当前消息、待显示队列、
  最小/最大显示 tick 判定、锚点与位置解算、透明度目标、绘制批次。
- ``PointerEvent`` / ``PointerClick``：中立指针事件与它翻译出的产品意图。
- ``AnchorPlacement``：``(屏幕锚点, 自身尺寸, 屏幕矩形) -> 窗口左上角``。

本模块是后端中立的：不 import PyQt5，也不 import 任何 ``backends/*``。真实窗口、
透明度动画、剪贴板与 z-order 由后端窗口宿主持有（Qt 见
``lib/core/render/backends/qt/widgets/control_host.py``）。跨后端对齐的事实源是绘制批次：
``build_bubble_visual`` 在两个后端上产出同一份批次，描述层只决定"何时、何地、多透明"
把它交出去。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.core.render.visuals.application_visuals import (
    BubbleVisualDescription,
    build_bubble_visual,
    resolve_bubble_geometry,
)
from lib.core.render.visuals.types import Point, Rect, Size

if TYPE_CHECKING:
    from lib.core.render.backends.base import TextMetrics

__all__ = [
    "AnchorPlacement",
    "BubbleControl",
    "BubbleInfo",
    "PointerClick",
    "PointerEvent",
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


@dataclass(frozen=True, slots=True)
class AnchorPlacement:
    """锚点对齐解算结果：窗口左上角与所在屏幕。"""

    x: int
    y: int
    screen: Rect


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
        raw = self._opacity_scale() if callable(self._opacity_scale) else self._opacity_scale
        try:
            scale = float(raw)
        except (TypeError, ValueError):
            scale = 1.0
        base = max(0.0, min(1.0, float(target)))
        return max(0.0, min(1.0, base * max(0.0, min(1.0, scale))))

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
