"""排布解算的共享事实源：把一个窗口「贴」到另一块几何上的哪一边。

控件层此前各自实现同一套锚点算术：取目标矩形的某个锚点、取自身窗口的某个锚点、
两者相减、加上偏移、再 ``clamp_rect_position`` 夹回屏幕。命令框附属按钮、麦克风
指示器、进度条、公告/更新/帮助/语音包浮窗都在重复这五步，只是锚点名字与偏移值不同。

本模块把这五步收成一份声明：

- ``PlacementSpec``：描述「把自身 ``self_anchor_id`` 对到目标矩形的 ``target_anchor_id``，
  再按 ``offset_x`` / ``offset_y`` 平移」；
- ``resolve_placement()``：解算并夹取，返回 ``AnchorPlacement``（窗口左上角 + 所在屏幕）。

本模块属《render 层边界契约》的共享事实源（与 ``anchors.py`` / ``screen.py`` 同级）：
只依赖后端中立几何，不 import PyQt5、任一 ``backends/*``、``lib.script`` 或 ``config.config_ui``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.core.render.visuals.anchors import get_anchor_point
from lib.core.render.visuals.screen import clamp_rect_position
from lib.core.render.visuals.types import Point, Rect, coerce_point, coerce_rect, coerce_size

if TYPE_CHECKING:
    from typing import Protocol

    class SelfSize(Protocol):
        width: float
        height: float


#: 锚点名字：与 ``get_anchor_point`` 支持的表一致，未知名字按中心处理。
ANCHORS = (
    "top", "bottom", "left", "right",
    "top_left", "top_right", "bottom_left", "bottom_right",
    "center",
)


@dataclass(frozen=True, slots=True)
class AnchorPlacement:
    """锚点对齐解算结果：窗口左上角与所在屏幕。"""

    x: int
    y: int
    screen: Rect


def coerce_self_size(size: object) -> tuple[int, int]:
    """把 ``Size`` / ``(w, h)`` / 任意带 ``width``/``height`` 的对象统一成整数宽高。

    形状对齐 ``RectActionButtonControl`` 的既有输入面（核心 ``Size`` 与 Qt 的取值对象
    都能喂进来），两个后端共用同一条落位路径时不额外引入类型断言。
    """
    resolved = coerce_size(size)
    if resolved is None:
        raise TypeError(f"placement requires a size-like value, got {size!r}")
    return max(1, int(round(resolved.width))), max(1, int(round(resolved.height)))


@dataclass(frozen=True, slots=True)
class PlacementSpec:
    """一次锚点对齐的声明：目标锚点、自身锚点、偏移。

    默认值 ``target_anchor_id="top_left"`` / ``self_anchor_id="bottom_left"`` 就是命令框
    附属按钮族用了很久的那一对；其余控件按自己的语义覆盖即可。
    """

    target_anchor_id: str = "top_left"
    self_anchor_id: str = "bottom_left"
    offset_x: float = 0.0
    offset_y: float = 0.0

    def resolve_point(
        self,
        self_size: "SelfSize | tuple[float, float]",
        target_rect: Rect,
    ) -> Point:
        """解算窗口左上角，但**不**夹取。

        ``target_rect`` 与返回值同处一个坐标系（屏幕坐标）；``get_anchor_point`` 返回的
        锚点已经带上目标矩形的原点，因此这里只做「目标锚点 − 自身锚点 + 偏移」。
        """
        width, height = coerce_self_size(self_size)
        target = coerce_rect(target_rect)
        if target is None:
            raise TypeError(f"placement requires a rect-like target, got {target_rect!r}")
        target_anchor = get_anchor_point(target, self.target_anchor_id)
        self_anchor = get_anchor_point(
            Rect(0.0, 0.0, float(width), float(height)), self.self_anchor_id
        )
        return Point(
            target_anchor.x - self_anchor.x + float(self.offset_x),
            target_anchor.y - self_anchor.y + float(self.offset_y),
        )

    def resolve_placement(
        self,
        self_size: "SelfSize | tuple[float, float]",
        target_rect: Rect,
        screen: Rect,
    ) -> AnchorPlacement:
        """解算窗口左上角并夹取回 ``screen``。"""
        width, height = coerce_self_size(self_size)
        position = self.resolve_point(self_size, target_rect)
        clamped_x, clamped_y, _ = clamp_rect_position(
            int(round(position.x)), int(round(position.y)), width, height, screen
        )
        return AnchorPlacement(clamped_x, clamped_y, screen)

    def resolve_from_point(
        self,
        self_size: "SelfSize | tuple[float, float]",
        anchor_point: Point,
        screen: Rect,
    ) -> AnchorPlacement:
        """目标是一个**点**（上游已经算出自己的某个锚点全局坐标）时的落位。

        命令框附属按钮与麦克风指示器只拿得到上游窗口的全局矩形，再自己取锚点；把
        锚点当作零尺寸矩形即可复用同一套解算，不必为「点目标」再造一条路径。
        点会先经 ``coerce_point``，Qt 的取值对象可以直接喂进来。
        """
        point = coerce_point(anchor_point)
        if point is None:
            raise TypeError(f"placement requires a point-like anchor, got {anchor_point!r}")
        return self.resolve_placement(self_size, Rect(point.x, point.y, 0.0, 0.0), screen)

    def resolve_centered(
        self,
        self_size: "SelfSize | tuple[float, float]",
        screen: Rect,
    ) -> AnchorPlacement:
        """在 ``screen`` 里居中，再夹取（公告/更新/帮助/语音包浮窗的公共落位）。"""
        width, height = coerce_self_size(self_size)
        x = int(screen.x) + (int(screen.width) - width) // 2
        y = int(screen.y) + (int(screen.height) - height) // 2
        clamped_x, clamped_y, _ = clamp_rect_position(x, y, width, height, screen)
        return AnchorPlacement(clamped_x, clamped_y, screen)


def resolve_placement(
    self_size: "SelfSize | tuple[float, float]",
    target_rect: Rect,
    screen: Rect,
    *,
    target_anchor_id: str = "top_left",
    self_anchor_id: str = "bottom_left",
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> AnchorPlacement:
    """``PlacementSpec.resolve_placement`` 的函数形态，等价于显式构造一个 spec。"""
    return PlacementSpec(
        target_anchor_id=target_anchor_id,
        self_anchor_id=self_anchor_id,
        offset_x=offset_x,
        offset_y=offset_y,
    ).resolve_placement(self_size, target_rect, screen)


__all__ = [
    "ANCHORS",
    "AnchorPlacement",
    "PlacementSpec",
    "coerce_self_size",
    "resolve_placement",
]
