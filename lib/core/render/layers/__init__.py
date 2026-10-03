"""图层：绘制层与顶层窗口 z-order 的单一落点。

上游是 `config/config_layer.py` 的 `LAYER_VALUES`，下游只被业务层与两个后端消费：

- `spec.Layer`    一处画布内部的绘制层（`DrawBatch` / `DrawScene`）。
- `order`         绘制排序原语，`visuals/` 的下层。
- `draws`         层内 z 槽（`BASE`..`OVERLAY_THIRD` 一条递增阶梯）。
- `WindowsLayerManager` / `WindowLayer` / `hosts`  顶层窗口的整窗层级与宿主协议。

引用规则：业务层与 `visuals/` 只从本包取图层能力，不再直接引用散落的层级模块。

`visuals/` 依赖 `spec` / `order` / `draws` 这三块纯数据，因此它们在本模块顶层
立即导入；窗口管理器与宿主协议会反过来依赖 `visuals.types`，改用惰性取值
（PEP 562），否则 `visuals -> layers -> windows -> registry -> visuals` 会成环。
"""
from importlib import import_module

from .spec import Layer
from .order import draw_order_key, layer_name, normalize_layer, order_render_values
from .draws import (
    BASE,
    CONTENT,
    FRAME,
    INNER,
    MIDDLE,
    OVERLAY,
    OVERLAY_SECOND,
    OVERLAY_THIRD,
)

#: 惰性导出：符号 -> (子模块, 该模块内的名字)。
_LAZY_EXPORTS = {
    "WindowLayer": ("windows", "WindowLayer"),
    "LayerWindow": ("windows", "LayerWindow"),
    "WindowsLayerManager": ("windows", "WindowsLayerManager"),
    "LayerManager": ("windows", "WindowsLayerManager"),
    "get_layer_manager": ("windows", "get_layer_manager"),
    "cleanup_layer_manager": ("windows", "cleanup_layer_manager"),
    "LayerWindowHost": ("hosts", "LayerWindowHost"),
    "LayerWindowHostFactory": ("hosts", "LayerWindowHostFactory"),
    "WindowHost": ("hosts", "WindowHost"),
    "WindowHostFactory": ("hosts", "WindowHostFactory"),
    "PassiveLayerWindowHost": ("hosts", "PassiveLayerWindowHost"),
    "PassiveWindowHost": ("hosts", "PassiveWindowHost"),
    "create_passive_layer_window_host": ("hosts", "create_passive_layer_window_host"),
    "create_passive_window_host": ("hosts", "create_passive_window_host"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{target[0]}")
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "Layer",
    "WindowLayer",
    "LayerWindow",
    "WindowsLayerManager",
    "LayerManager",
    "get_layer_manager",
    "cleanup_layer_manager",
    "normalize_layer",
    "layer_name",
    "draw_order_key",
    "order_render_values",
    "LayerWindowHost",
    "LayerWindowHostFactory",
    "WindowHost",
    "WindowHostFactory",
    "PassiveLayerWindowHost",
    "PassiveWindowHost",
    "create_passive_layer_window_host",
    "create_passive_window_host",
    "BASE",
    "FRAME",
    "INNER",
    "MIDDLE",
    "CONTENT",
    "OVERLAY",
    "OVERLAY_SECOND",
    "OVERLAY_THIRD",
]
