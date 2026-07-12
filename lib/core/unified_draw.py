"""统一绘制模块门面。

业务代码新增可视对象时优先从这里获取层级/绘制入口，避免继续分散依赖
DrawCore、LayerManager、TopmostManager 的具体实现。
"""

from lib.core.draw_core import DrawCore, DrawRequest, get_draw_core
from lib.core.layer import Layer, draw_order_key, layer_name, normalize_layer
from lib.core.layer_manager import LayerManager, get_layer_manager
from lib.core.render_core import RenderCore, get_render_core, order_render_values
from lib.core.render_layer import RenderItem, RenderRequest

__all__ = [
    'DrawCore',
    'DrawRequest',
    'Layer',
    'LayerManager',
    'RenderCore',
    'RenderItem',
    'RenderRequest',
    'get_draw_core',
    'get_layer_manager',
    'get_render_core',
    'order_render_values',
    'draw_order_key',
    'layer_name',
    'normalize_layer',
]
