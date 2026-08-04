"""Qt implementation of the backend-neutral world-object facade."""
from __future__ import annotations

from pathlib import Path

from lib.core.graphics.types import Point, Rect
from lib.core.qt_bridge import world_object_assets
from lib.core.qt_bridge.world_object_factory import create_world_object
from lib.core.world_objects import WorldObjectImagePair

_WORLD_OBJECT_TYPES = {
    "motor": ("lib.core.qt_bridge.world_objects.motor", "Mortor"),
    "clock": ("lib.core.qt_bridge.world_objects.clock", "Clock"),
    "sofa": ("lib.core.qt_bridge.world_objects.sofa", "Sofa"),
    "snow_pile": ("lib.core.qt_bridge.world_objects.snow_pile", "SnowPile"),
    "snowball": ("lib.core.qt_bridge.world_objects.snowball", "Snowball"),
    "snow_leopard": ("lib.core.qt_bridge.world_objects.snow_leopard", "SnowLeopard"),
    "speaker": ("lib.core.qt_bridge.world_objects.speaker", "Speaker"),
}


def _pair(value) -> WorldObjectImagePair | None:
    if value is None:
        return None
    return WorldObjectImagePair(value.pixmap, value.flipped_pixmap, value.size)


class QtWorldObjectBackend:
    def load_image(self, path: str | Path) -> object | None:
        return world_object_assets.load_pixmap(path)

    def scale_image(self, image: object, size: tuple[int, int]) -> object:
        return world_object_assets.scale_pixmap(image, size)

    def scale_image_keep_aspect(self, image: object, size: tuple[int, int]) -> object:
        return world_object_assets.scale_pixmap_keep_aspect(image, size)

    def image_size(self, image: object) -> tuple[int, int]:
        return int(image.width()), int(image.height())

    def load_stretched_image_pair(
        self,
        path: str | Path,
        size: tuple[int, int],
    ) -> WorldObjectImagePair | None:
        return _pair(world_object_assets.load_stretched_pixmap_pair(path, size))

    def load_height_scaled_image_pair(
        self,
        path: str | Path,
        height: int,
    ) -> WorldObjectImagePair | None:
        return _pair(world_object_assets.load_height_scaled_pixmap_pair(path, height))

    def load_width_scaled_image_pair(
        self,
        path: str | Path,
        width: int,
    ) -> WorldObjectImagePair | None:
        return _pair(world_object_assets.load_width_scaled_pixmap_pair(path, width))

    def load_gif_frame_pair(self, path: str | Path) -> tuple[list[object], list[object]]:
        return world_object_assets.load_gif_frame_pair(path)

    def create(
        self,
        object_type: str,
        *,
        position: Point,
        **kwargs,
    ) -> object:
        try:
            module_name, class_name = _WORLD_OBJECT_TYPES[object_type]
        except KeyError as exc:
            raise ValueError(f"unknown world-object type: {object_type}") from exc
        constructor_args = dict(kwargs)
        if "image" in constructor_args:
            constructor_args["pixmap"] = constructor_args.pop("image")
        if "flipped_image" in constructor_args:
            constructor_args["flipped_pixmap"] = constructor_args.pop("flipped_image")
        return create_world_object(
            module_name,
            class_name,
            position=position,
            **constructor_args,
        )

    def get_center(self, instance: object) -> Point:
        center = instance.get_center()
        return Point(float(center.x()), float(center.y()))

    def get_geometry(self, instance: object) -> Rect:
        geometry = instance.geometry()
        return Rect(
            float(geometry.x()),
            float(geometry.y()),
            float(geometry.width()),
            float(geometry.height()),
        )
