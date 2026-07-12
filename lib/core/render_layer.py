"""统一绘制队列数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QPainter

from lib.core.layer import Layer, normalize_layer


PaintCallback = Callable[[QPainter, Optional[QRect]], None]


@dataclass
class RenderItem:
    """单个可绘制项。"""

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
    """注册/更新绘制项的请求。"""

    item_id: str
    paint: PaintCallback
    layer: int = field(default_factory=lambda: int(Layer.MAIN_PET))
    z: int = 0
    visible: bool = True
