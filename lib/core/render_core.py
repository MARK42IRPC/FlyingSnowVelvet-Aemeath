"""统一绘制队列核心。"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Optional, TypeVar

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QPainter

from lib.core.layer import Layer, draw_order_key
from lib.core.render_layer import RenderItem, RenderRequest


_RenderValue = TypeVar('_RenderValue')


def order_render_values(
    values: Iterable[_RenderValue],
    *,
    layer_getter: Callable[[_RenderValue], object],
    z_getter: Callable[[_RenderValue], object],
    order_getter: Callable[[_RenderValue], object],
    default_layer: Layer = Layer.MAIN_PET,
) -> list[_RenderValue]:
    """按统一 layer/z/生成顺序排列任意绘制对象。"""
    return sorted(
        values,
        key=lambda value: draw_order_key(
            layer_getter(value),
            z_getter(value),
            order_getter(value),
            default_layer,
        ),
    )


class RenderCore:
    """管理非窗口级绘制项的统一排序与渲染。"""

    def __init__(self) -> None:
        self._items: dict[str, RenderItem] = {}
        self._seq: int = 0

    def register_item(self, request: RenderRequest) -> None:
        """注册或更新绘制项。"""
        existing = self._items.get(request.item_id)
        order = existing.order if existing is not None else self._next_order()
        self._items[request.item_id] = RenderItem(
            item_id=request.item_id,
            paint=request.paint,
            layer=request.layer,
            z=request.z,
            visible=request.visible,
            order=order,
        )

    def remove_item(self, item_id: str) -> None:
        """移除绘制项。"""
        self._items.pop(item_id, None)

    def set_visible(self, item_id: str, visible: bool) -> None:
        """设置绘制项可见性。"""
        item = self._items.get(item_id)
        if item is not None:
            item.visible = bool(visible)

    def clear(self) -> None:
        """清空绘制队列。"""
        self._items.clear()

    def render(self, painter: QPainter, target_rect: Optional[QRect] = None) -> None:
        """按 layer/z/生成顺序渲染，顺序更晚的同级绘制项覆盖在上。"""
        if not self._items:
            return

        for item in sorted(
            self._items.values(),
            key=lambda value: draw_order_key(value.layer, value.z, value.order, Layer.MAIN_PET),
        ):
            if not item.visible:
                continue
            painter.save()
            try:
                item.paint(painter, target_rect)
            finally:
                painter.restore()

    def _next_order(self) -> int:
        self._seq += 1
        return self._seq


_INSTANCE: RenderCore | None = None


def get_render_core() -> RenderCore:
    """返回全局 RenderCore。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = RenderCore()
    return _INSTANCE


def cleanup_render_core() -> None:
    """清理全局 RenderCore。"""
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.clear()
    _INSTANCE = None
