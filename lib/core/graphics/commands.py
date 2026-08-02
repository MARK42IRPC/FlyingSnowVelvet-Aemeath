"""Qt-independent draw and render command data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from lib.core.layer import Layer, normalize_layer
from .types import Point


@dataclass
class DrawRequest:
    """A resource draw request understood by any graphics backend."""

    resource_id: str
    frame_index: int = -1
    position: Point | tuple[int, int] | None = None
    alpha: float = 1.0
    flipped: bool = False
    scale: float = 1.0
    layer: int = int(Layer.MAIN_PET)
    z: int = 0
    order: int = 0


PaintCallback = Callable[[object, object | None], None]


@dataclass
class RenderItem:
    """A registered backend-owned paint callback."""

    item_id: str
    paint: PaintCallback
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
class RenderRequest:
    """A request to register or update a render item."""

    item_id: str
    paint: PaintCallback
    layer: int = field(default_factory=lambda: int(Layer.MAIN_PET))
    z: int = 0
    visible: bool = True
