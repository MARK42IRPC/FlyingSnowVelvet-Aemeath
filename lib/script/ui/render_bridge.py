"""供 `lib/script/ui` 控件使用的后端无关渲染适配。

产品控件本身是 QWidget，绘制时要拿到两样东西：一个绘制实现，和 QWidget 画完之后
能交给它去画的图像对象。这两件事都是 Qt 事实，但它们出现在控件代码里就会让控件直接
依赖 `lib/core/render/backends/qt/drawing/`，那正是 `doc/render层边界契约.md` 档位 A
要挡住的引用——一旦控件能自己 new 出 Qt 绘制实现，"换后端不动业务层"就无从谈起。

所以控件只问本模块要能力，本模块是唯一允许触碰到具体实现的 UI 侧落点：

- 已配置后端（正常运行时）→ 取后端注册进 registry 的绘制实现；
- 未配置后端（控件单元测试、隔离 helper）→ 直接构造 Qt 实现。

第二种情况看着像"偷偷用 Qt"，其实是刻意的：Qt 是产品唯一受支持的后端，控件测试要
比对真实像素就必须有真实绘制实现。回退到空实现会让控件画不出东西（历史上正是如此），
那既掩盖回归也让像素断言失去意义。本模块的存在把这一事实收敛到一处，而不是让它
散落在二十多个控件文件里。

同一份理由适用于这里的颜色与坐标转换：色板事实源在 `visuals/palette.py`，但 QWidget
要的是 `QColor`，锚点计算要的是 `QPoint`。把这两件 Qt 事实也收进本模块，控件就不必
为了一个 `UI_THEME['mid']` 或一次 `coerce_qpoint` 去直接 import 绘制档。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lib.core.render.registry import get_draw_backend_factory

if TYPE_CHECKING:
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QColor


def create_draw_backend():
    """返回当前后端的绘制实现；没有已配置后端时返回 Qt 实现。"""
    factory = get_draw_backend_factory()
    if factory is not None:
        return factory()
    from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend

    return QtDrawBackend()


def create_component_layer():
    """创建控件自用的绘制回调层（排序与注册是后端无关的，只有回调是 Qt）。"""
    from lib.core.render.backends.qt.drawing.render_core import QtComponentLayer

    return QtComponentLayer()


def create_component_layer_request(item_id, paint, layer: int = 0, z: int = 0, visible: bool = True):
    """构造一条绘制回调层注册请求。"""
    from lib.core.render.backends.qt.drawing.render_core import QtComponentLayerRequest

    return QtComponentLayerRequest(item_id, paint, layer=layer, z=z, visible=visible)


def qimage_from_raster_frame(frame):
    """把核心 RGBA 帧转换成 QWidget 能直接绘制的 Qt 图像对象。"""
    from lib.core.render.backends.qt.drawing.gif_loader import qimage_from_raster_frame as convert

    return convert(frame)


def qt_color(token: str) -> QColor:
    """取主题色的 Qt 表示：先查 `COLORS`，再查 `UI_THEME`。"""
    from lib.core.render.backends.qt.drawing.colors import to_qcolor
    from lib.core.render.visuals.palette import COLORS, UI_THEME

    table = COLORS if token in COLORS else UI_THEME
    return to_qcolor(table[token])


def qt_color_name(token: str) -> str:
    """取主题色的 `#rrggbb` 文本，用于 QSS 拼装。"""
    return qt_color(token).name()


def ensure_qcolor(value: object) -> QColor:
    """把 QColor / 核心 Color / RGBA 元组统一成 QColor。"""
    from lib.core.render.backends.qt.drawing.colors import to_qcolor

    return to_qcolor(value)


def qpoint_from_point(value: object) -> QPoint | None:
    """把锚点或坐标载荷转换成 QPoint；无效输入返回 None。"""
    from lib.core.render.backends.qt.drawing.window import coerce_qpoint

    return coerce_qpoint(value)
