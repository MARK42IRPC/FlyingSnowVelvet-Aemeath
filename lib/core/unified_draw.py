"""统一绘制模块门面。

业务代码新增可视对象时优先从这里获取绘制入口，避免继续分散依赖 DrawCore
的具体实现。图层（`Layer` / `WindowLayer` / 窗口管理器）统一从
`lib.core.render.layers` 取得，本模块不再二次转发，避免出现第二个层级来源。
"""

from lib.core.draw_core import DrawCore, get_draw_core
from lib.core.render.layers import (
    Layer,
    WindowLayer,
    draw_order_key,
    get_layer_manager,
    layer_name,
    normalize_layer,
    order_render_values,
)
from lib.core.render.visuals.commands import DrawRequest

__all__ = [
    'DrawCore',
    'DrawRequest',
    'Layer',
    'WindowLayer',
    'get_draw_core',
    'get_layer_manager',
    'order_render_values',
    'draw_order_key',
    'layer_name',
    'normalize_layer',
]
