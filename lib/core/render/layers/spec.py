"""画布绘制层：一处 `DrawBatch` 内部的先后顺序。

数值越大越靠前。值来自 `config/config_layer.py` 的 `LAYER_VALUES`，
可以在控制面板「系统调度 -> 图层顺序」里修改，保存后需要重启生效。
同一层且 z 相同时，组件按生成顺序后来居上。
"""
from enum import IntEnum

try:
    from config.config_layer import LAYER_VALUES as _LAYER_VALUES
except (ImportError, TypeError, ValueError):
    _LAYER_VALUES = {}


#: `config_layer` 是唯一事实源；这里只是导入失败时的兜底，必须与之一致。
_DEFAULT_LAYER_VALUES = {
    'BACKGROUND': 0,
    'WORLD_OBJECT': 100,
    'MAIN_PET': 850,
    'PET_EFFECT_BELOW': 250,
    'PARTICLE': 650,
    'EFFECT': 660,
    'PET_UI': 640,
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
    """绘制层：同一画布内元素的全局层级，数值越大越靠前。"""

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


__all__ = ['Layer']
