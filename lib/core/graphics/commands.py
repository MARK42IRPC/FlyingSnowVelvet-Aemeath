"""Backend-neutral draw request and immutable frame command data."""
from __future__ import annotations

from dataclasses import dataclass

from lib.core.layer import Layer, normalize_layer
from .resources import RasterFrame
from .types import Point, coerce_point


@dataclass
class DrawRequest:
    """Mutable scene state used to select and place one image resource."""

    resource_id: str
    frame_index: int = -1
    position: Point | None = None
    alpha: float = 1.0
    flipped: bool = False
    scale: float = 1.0
    layer: int = int(Layer.MAIN_PET)
    z: int = 0
    order: int = 0

    def __post_init__(self) -> None:
        self.resource_id = str(self.resource_id or "").strip()
        if not self.resource_id:
            raise ValueError("draw request resource id must not be empty")
        try:
            self.frame_index = int(self.frame_index)
        except (TypeError, ValueError):
            self.frame_index = -1
        if self.position is not None:
            point = coerce_point(self.position)
            if point is None:
                raise TypeError(f"invalid draw request position: {self.position!r}")
            self.position = point
        try:
            self.alpha = max(0.0, min(1.0, float(self.alpha)))
        except (TypeError, ValueError):
            self.alpha = 1.0
        self.flipped = bool(self.flipped)
        try:
            self.scale = max(0.0, float(self.scale))
        except (TypeError, ValueError):
            self.scale = 1.0
        self.layer = normalize_layer(self.layer, Layer.MAIN_PET)
        try:
            self.z = int(self.z)
        except (TypeError, ValueError):
            self.z = 0
        try:
            self.order = int(self.order)
        except (TypeError, ValueError):
            self.order = 0


@dataclass(frozen=True, slots=True)
class SpriteCommand:
    """One resolved sprite operation ready for a graphics backend."""

    resource_id: str
    resource_revision: int
    frame_index: int
    frame: RasterFrame
    position: Point | None
    alpha: float
    flipped: bool
    scale: float
    layer: int
    z: int
    order: int

    def __post_init__(self) -> None:
        resource_id = str(self.resource_id or "").strip()
        if not resource_id:
            raise ValueError("sprite command resource id must not be empty")
        if not isinstance(self.frame, RasterFrame):
            raise TypeError("sprite command frame must be a RasterFrame")
        if self.position is not None and not isinstance(self.position, Point):
            raise TypeError("sprite command position must be a Point or None")
        object.__setattr__(self, "resource_id", resource_id)
        object.__setattr__(self, "resource_revision", max(1, int(self.resource_revision)))
        object.__setattr__(self, "frame_index", max(0, int(self.frame_index)))
        object.__setattr__(self, "alpha", max(0.0, min(1.0, float(self.alpha))))
        object.__setattr__(self, "flipped", bool(self.flipped))
        object.__setattr__(self, "scale", max(0.0, float(self.scale)))
        object.__setattr__(self, "layer", normalize_layer(self.layer, Layer.MAIN_PET))
        object.__setattr__(self, "z", int(self.z))
        object.__setattr__(self, "order", int(self.order))


@dataclass(frozen=True, slots=True)
class ResourceRevision:
    """The current revision of one resource registered in a scene."""

    resource_id: str
    revision: int

    def __post_init__(self) -> None:
        resource_id = str(self.resource_id or "").strip()
        if not resource_id:
            raise ValueError("resource revision id must not be empty")
        object.__setattr__(self, "resource_id", resource_id)
        object.__setattr__(self, "revision", max(1, int(self.revision)))


@dataclass(frozen=True, slots=True)
class DrawBatch:
    """An immutable, ordered set of commands for one paint pass."""

    commands: tuple[SpriteCommand, ...] = ()
    resource_revisions: tuple[ResourceRevision, ...] = ()

    def __post_init__(self) -> None:
        commands = tuple(self.commands)
        resource_revisions = tuple(self.resource_revisions)
        if any(not isinstance(command, SpriteCommand) for command in commands):
            raise TypeError("draw batch commands must be SpriteCommand values")
        if any(
            not isinstance(resource, ResourceRevision)
            for resource in resource_revisions
        ):
            raise TypeError("draw batch resources must be ResourceRevision values")
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "resource_revisions", resource_revisions)
