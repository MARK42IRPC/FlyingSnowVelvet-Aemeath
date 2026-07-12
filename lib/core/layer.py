"""统一绘制/窗口层级定义。"""
from enum import IntEnum

try:
    from config.config_layer import LAYER_VALUES as _LAYER_VALUES
except (ImportError, TypeError, ValueError):
    _LAYER_VALUES = {}


_DEFAULT_LAYER_VALUES = {
    'BACKGROUND': 0,
    'WORLD_OBJECT': 100,
    'MAIN_PET': 200,
    'PET_EFFECT_BELOW': 250,
    'PARTICLE': 650,
    'EFFECT': 660,
    'PET_UI': 500,
    'PANEL': 600,
    'DIALOG': 700,
    'TOOLTIP': 800,
    'SYSTEM_MODAL': 900,
}


def _configured_layer_value(name: str) -> int:
    default = _DEFAULT_LAYER_VALUES[name]
    try:
        return int(_LAYER_VALUES.get(name, default))
    except (TypeError, ValueError):
        return default


class Layer(IntEnum):
    """运行期可视对象的全局层级。数值越大越靠前。"""

    BACKGROUND = _configured_layer_value('BACKGROUND')
    WORLD_OBJECT = _configured_layer_value('WORLD_OBJECT')
    MAIN_PET = _configured_layer_value('MAIN_PET')
    PET_EFFECT_BELOW = _configured_layer_value('PET_EFFECT_BELOW')
    PARTICLE = _configured_layer_value('PARTICLE')
    EFFECT = _configured_layer_value('EFFECT')
    PET_UI = _configured_layer_value('PET_UI')
    PANEL = _configured_layer_value('PANEL')
    DIALOG = _configured_layer_value('DIALOG')
    TOOLTIP = _configured_layer_value('TOOLTIP')
    SYSTEM_MODAL = _configured_layer_value('SYSTEM_MODAL')


def normalize_layer(layer, default: Layer = Layer.PET_UI) -> int:
    """将 Layer/int/str 统一转换为可排序的层级整数。"""
    if isinstance(layer, Layer):
        return int(layer)
    if isinstance(layer, str):
        name = layer.strip().upper()
        if name in Layer.__members__:
            return int(Layer[name])
    try:
        return int(layer)
    except (TypeError, ValueError):
        return int(default)


def layer_name(layer) -> str:
    """返回层级名称，未知数值返回原始数值字符串。"""
    value = normalize_layer(layer)
    try:
        return Layer(value).name
    except ValueError:
        return str(value)


def draw_order_key(layer, z=0, order=0, default: Layer = Layer.PET_UI) -> tuple[int, int, int]:
    """返回统一绘制排序键；同层同 z 时后生成对象后来居上。"""
    try:
        z_value = int(z)
    except (TypeError, ValueError):
        z_value = 0
    try:
        order_value = int(order)
    except (TypeError, ValueError):
        order_value = 0
    return normalize_layer(layer, default), z_value, order_value
