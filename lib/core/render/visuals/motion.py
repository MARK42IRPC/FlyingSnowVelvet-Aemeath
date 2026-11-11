"""Backend-neutral motion parameters for the workbench shell.

《视觉表现契约》第 3 节把「动画时序、缓动、插值」列为必须位于后端之外的视觉逻辑。
工作台的切页过渡、侧边栏抽屉和设置页分区错峰此前各自在 Qt 宿主里写死
``UI.get("ui_fade_duration", 180)`` 并按需乘系数，DirectX 后端若要表现同一套动效
只能再抄一份数字。

这里只做纯数值派生：输入是共享的 ``ui_fade_duration``（毫秒），输出是每种动效的
时长与错峰步长。缓动给的是名字而不是 Qt 枚举，宿主自行映射——本模块不 import
``PyQt5``，也不 import 任何 backend 或产品模块。

时长一律夹在显式区间内：用户把 ``ui_fade_duration`` 调到 5000ms 时，切页与抽屉
不该跟着变成 5 秒；调到 0 时也不该退化成「没有动效」之外更奇怪的负值。
"""

from __future__ import annotations

from lib.core.services.ui_presentation import ui_fade_duration_ms

#: 动画名（宿主映射成各自的缓动枚举）。
EASING_PAGE_TRANSITION = "in_out_quad"
EASING_GROUP_COLLAPSE = "in_out_cubic"
EASING_SECTION_ENTER = "out_cubic"

#: 时长区间（毫秒）。
PAGE_TRANSITION_RANGE = (150, 320)
GROUP_COLLAPSE_RANGE = (140, 260)
SECTION_ENTER_RANGE = (120, 240)

#: 分区错峰：相邻卡片之间额外等待的毫秒数，以及最多参与错峰的分区数。
SECTION_STAGGER_MS = 32
SECTION_STAGGER_LIMIT = 8

#: 抽屉展开时组内容的最终透明度；从它淡到 1.0，避免整块内容「啪」地出现。
GROUP_CONTENT_MIN_OPACITY = 0.35


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    low, high = bounds
    return max(low, min(high, int(value)))


def _fade_ms(fade_ms: int | None) -> int:
    """共享淡入淡出时长；``None`` 走 ``ui_fade_duration_ms()`` 的唯一事实源。"""
    return ui_fade_duration_ms(fade_ms)


def page_transition_ms(fade_ms: int | None = None) -> int:
    """整页切换（遮罩交叉淡出）的时长。"""
    return _clamp(_fade_ms(fade_ms), PAGE_TRANSITION_RANGE)


def group_collapse_ms(fade_ms: int | None = None) -> int:
    """侧边栏分组抽屉展开/收起的时长；比整页淡出略快一点。"""
    return _clamp(int(round(_fade_ms(fade_ms) * 0.9)), GROUP_COLLAPSE_RANGE)


def section_enter_ms(order: int, fade_ms: int | None = None) -> int:
    """第 ``order`` 个设置分区淡入的总时长。

    错峰靠延长时长实现：所有分区同时从 0 开始淡入，第 i 个分区慢一点结束，
    看上去就是「内容顺次浮现」。``QPropertyAnimation`` 没有 delay 属性，
    这是不引入额外定时器的等价做法。
    """
    base = _clamp(int(round(_fade_ms(fade_ms) * 0.9)), SECTION_ENTER_RANGE)
    return base + max(0, int(order)) * SECTION_STAGGER_MS


__all__ = [
    "EASING_GROUP_COLLAPSE",
    "EASING_PAGE_TRANSITION",
    "EASING_SECTION_ENTER",
    "GROUP_COLLAPSE_RANGE",
    "GROUP_CONTENT_MIN_OPACITY",
    "PAGE_TRANSITION_RANGE",
    "SECTION_ENTER_RANGE",
    "SECTION_STAGGER_LIMIT",
    "SECTION_STAGGER_MS",
    "group_collapse_ms",
    "page_transition_ms",
    "section_enter_ms",
]
