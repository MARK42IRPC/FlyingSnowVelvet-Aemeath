"""Qt-local painter callback queue for toolkit-owned windows."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from lib.core.layer import Layer, draw_order_key, normalize_layer


QtPaintCallback = Callable[[object, object | None], None]


@dataclass
class QtRenderItem:
    """A callback owned by a Qt widget and never exposed to core graphics."""

    item_id: str
    paint: QtPaintCallback
    layer: int = field(default_factory=lambda: int(Layer.MAIN_PET))
    z: int = 0
    visible: bool = True
    order: int = 0

    def __post_init__(self) -> None:
        self.layer = normalize_layer(self.layer, Layer.MAIN_PET)
        try:
            self.z = int(self.z)
        except (TypeError, ValueError):
            self.z = 0
        try:
            self.order = int(self.order)
        except (TypeError, ValueError):
            self.order = 0


@dataclass
class QtRenderRequest:
    """Register or update one Qt-local painter callback."""

    item_id: str
    paint: QtPaintCallback
    layer: int = field(default_factory=lambda: int(Layer.MAIN_PET))
    z: int = 0
    visible: bool = True


class QtRenderCore:
    """Sort and invoke painter callbacks for a single Qt widget."""

    def __init__(self) -> None:
        self._items: dict[str, QtRenderItem] = {}
        self._seq = 0

    def register_item(self, request: QtRenderRequest) -> None:
        existing = self._items.get(request.item_id)
        order = existing.order if existing is not None else self._next_order()
        self._items[request.item_id] = QtRenderItem(
            item_id=request.item_id,
            paint=request.paint,
            layer=request.layer,
            z=request.z,
            visible=request.visible,
            order=order,
        )

    def remove_item(self, item_id: str) -> None:
        self._items.pop(item_id, None)

    def set_visible(self, item_id: str, visible: bool) -> None:
        item = self._items.get(item_id)
        if item is not None:
            item.visible = bool(visible)

    def clear(self) -> None:
        self._items.clear()

    def render(self, painter: object, target_rect: object | None = None) -> None:
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
