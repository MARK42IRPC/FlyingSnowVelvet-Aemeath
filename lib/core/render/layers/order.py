"""绘制排序原语：把 layer / z / 生成顺序折成一个可比较的键。

这里是 `visuals/` 的下层：共享视觉层默认如何排序由本模块给出，
presenter 只负责提供数值。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from .spec import Layer


_RenderValue = TypeVar("_RenderValue")


def normalize_layer(layer, default: Layer = Layer.PET_UI) -> int:
    """将 Layer/int/str 统一转换为可排序的层整数。"""
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
    """返回层名称，未知数值返回原始数值字符串。"""
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


def order_render_values(
    values: Iterable[_RenderValue],
    *,
    layer_getter: Callable[[_RenderValue], object],
    z_getter: Callable[[_RenderValue], object],
    order_getter: Callable[[_RenderValue], object],
    default_layer: Layer = Layer.MAIN_PET,
) -> list[_RenderValue]:
    """Order arbitrary values by layer, z, and stable generation order."""
    return sorted(
        values,
        key=lambda value: draw_order_key(
            layer_getter(value),
            z_getter(value),
            order_getter(value),
            default_layer,
        ),
    )


__all__ = [
    'normalize_layer',
    'layer_name',
    'draw_order_key',
    'order_render_values',
]
