"""Backend-neutral world-object asset and construction facade."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lib.core.graphics.types import Point, Rect


@dataclass(frozen=True)
class WorldObjectImagePair:
    """Normal and mirrored backend image handles with their rendered size."""

    image: object
    flipped_image: object
    size: tuple[int, int]


class WorldObjectBackend(Protocol):
    def load_image(self, path: str | Path) -> object | None: ...

    def scale_image(self, image: object, size: tuple[int, int]) -> object: ...

    def scale_image_keep_aspect(self, image: object, size: tuple[int, int]) -> object: ...

    def image_size(self, image: object) -> tuple[int, int]: ...

    def load_stretched_image_pair(
        self,
        path: str | Path,
        size: tuple[int, int],
    ) -> WorldObjectImagePair | None: ...

    def load_height_scaled_image_pair(
        self,
        path: str | Path,
        height: int,
    ) -> WorldObjectImagePair | None: ...

    def load_width_scaled_image_pair(
        self,
        path: str | Path,
        width: int,
    ) -> WorldObjectImagePair | None: ...

    def load_gif_frame_pair(self, path: str | Path) -> tuple[list[object], list[object]]: ...

    def create(
        self,
        object_type: str,
        *,
        position: Point,
        **kwargs,
    ) -> object: ...

    def get_center(self, instance: object) -> Point: ...

    def get_geometry(self, instance: object) -> Rect: ...


_backend: WorldObjectBackend | None = None


def configure_world_object_backend(backend: WorldObjectBackend) -> None:
    global _backend
    _backend = backend


def get_world_object_backend() -> WorldObjectBackend | None:
    return _backend


def reset_world_object_backend() -> None:
    global _backend
    _backend = None


def _require_backend() -> WorldObjectBackend:
    if _backend is None:
        raise RuntimeError("world-object backend has not been configured")
    return _backend


def load_image(path: str | Path) -> object | None:
    return _require_backend().load_image(path)


def scale_image(image: object, size: tuple[int, int]) -> object:
    return _require_backend().scale_image(image, size)


def scale_image_keep_aspect(image: object, size: tuple[int, int]) -> object:
    return _require_backend().scale_image_keep_aspect(image, size)


def get_image_size(image: object) -> tuple[int, int]:
    return _require_backend().image_size(image)


def load_stretched_image_pair(
    path: str | Path,
    size: tuple[int, int],
) -> WorldObjectImagePair | None:
    return _require_backend().load_stretched_image_pair(path, size)


def load_height_scaled_image_pair(
    path: str | Path,
    height: int,
) -> WorldObjectImagePair | None:
    return _require_backend().load_height_scaled_image_pair(path, height)


def load_width_scaled_image_pair(
    path: str | Path,
    width: int,
) -> WorldObjectImagePair | None:
    return _require_backend().load_width_scaled_image_pair(path, width)


def load_gif_frame_pair(path: str | Path) -> tuple[list[object], list[object]]:
    return _require_backend().load_gif_frame_pair(path)


def create_world_object(
    object_type: str,
    *,
    position: Point,
    **kwargs,
) -> object:
    return _require_backend().create(
        object_type,
        position=position,
        **kwargs,
    )


def get_world_object_center(instance: object) -> Point:
    return _require_backend().get_center(instance)


def get_world_object_geometry(instance: object) -> Rect:
    return _require_backend().get_geometry(instance)
