"""Ordering helpers shared by backend-neutral draw state."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from lib.core.layer import Layer, draw_order_key


_RenderValue = TypeVar("_RenderValue")


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
