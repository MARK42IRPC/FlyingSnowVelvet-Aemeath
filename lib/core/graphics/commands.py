"""Backend-neutral draw request and immutable frame command data."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import IntFlag

from lib.core.layer import Layer, normalize_layer
from .resources import RasterFrame
from .types import (
    Color, FontSpec, Point, Rect, Size, coerce_color, coerce_point,
    coerce_rect, coerce_size,
)


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
    target_size: Size | None = None

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
        if self.target_size is not None:
            size = coerce_size(self.target_size)
            if size is None:
                raise TypeError(f"invalid draw request target size: {self.target_size!r}")
            self.target_size = Size(max(0.0, size.width), max(0.0, size.height))
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
    target_size: Size | None = None

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
        if self.target_size is not None:
            size = coerce_size(self.target_size)
            if size is None:
                raise TypeError("sprite command target size must be a Size or None")
            object.__setattr__(self, "target_size", Size(max(0.0, size.width), max(0.0, size.height)))
        object.__setattr__(self, "layer", normalize_layer(self.layer, Layer.MAIN_PET))
        object.__setattr__(self, "z", int(self.z))
        object.__setattr__(self, "order", int(self.order))


class TextAlignment(IntFlag):
    """Backend-neutral text alignment flags.

    Values intentionally match the corresponding Qt flags at the adapter
    boundary, while callers only need to depend on this core enum.
    """

    LEFT = 0x0001
    RIGHT = 0x0002
    HCENTER = 0x0004
    JUSTIFY = 0x0008
    TOP = 0x0020
    BOTTOM = 0x0040
    VCENTER = 0x0080
    WORD_WRAP = 0x1000


def _normalize_order_fields(instance: object, layer: object, z: object, order: object) -> None:
    object.__setattr__(instance, "layer", normalize_layer(layer, Layer.MAIN_PET))
    try:
        object.__setattr__(instance, "z", int(z))
    except (TypeError, ValueError):
        object.__setattr__(instance, "z", 0)
    try:
        object.__setattr__(instance, "order", int(order))
    except (TypeError, ValueError):
        object.__setattr__(instance, "order", 0)


def _normalize_alpha(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


@dataclass(frozen=True, slots=True)
class TextCommand:
    """One backend-neutral text draw operation."""

    text: str
    font: FontSpec
    color: Color
    rect: Rect
    alignment: int = int(TextAlignment.LEFT | TextAlignment.VCENTER)
    alpha: float = 1.0
    layer: int = int(Layer.MAIN_PET)
    z: int = 0
    order: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or ""))
        if not isinstance(self.font, FontSpec):
            raise TypeError("text command font must be a FontSpec")
        color = coerce_color(self.color)
        if color is None:
            raise TypeError("text command color must be a Color")
        rect = coerce_rect(self.rect)
        if rect is None:
            raise TypeError("text command rect must be a Rect")
        object.__setattr__(self, "color", color)
        object.__setattr__(self, "rect", rect)
        object.__setattr__(self, "alignment", int(self.alignment))
        object.__setattr__(self, "alpha", _normalize_alpha(self.alpha))
        _normalize_order_fields(self, self.layer, self.z, self.order)


@dataclass(frozen=True, slots=True)
class LineCommand:
    """One backend-neutral line draw operation."""

    start: Point
    end: Point
    color: Color
    width: float = 1.0
    alpha: float = 1.0
    layer: int = int(Layer.MAIN_PET)
    z: int = 0
    order: int = 0

    def __post_init__(self) -> None:
        start = coerce_point(self.start)
        end = coerce_point(self.end)
        color = coerce_color(self.color)
        if start is None or end is None:
            raise TypeError("line command endpoints must be Point values")
        if color is None:
            raise TypeError("line command color must be a Color")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "color", color)
        try:
            width = float(self.width)
        except (TypeError, ValueError):
            width = 1.0
        object.__setattr__(self, "width", max(0.0, width))
        object.__setattr__(self, "alpha", _normalize_alpha(self.alpha))
        _normalize_order_fields(self, self.layer, self.z, self.order)


@dataclass(frozen=True, slots=True)
class RectCommand:
    """One backend-neutral rectangle draw operation."""

    rect: Rect
    fill: Color | None = None
    stroke: Color | None = None
    stroke_width: float = 1.0
    alpha: float = 1.0
    layer: int = int(Layer.MAIN_PET)
    z: int = 0
    order: int = 0

    def __post_init__(self) -> None:
        rect = coerce_rect(self.rect)
        fill = coerce_color(self.fill) if self.fill is not None else None
        stroke = coerce_color(self.stroke) if self.stroke is not None else None
        if rect is None:
            raise TypeError("rect command rect must be a Rect")
        if self.fill is not None and fill is None:
            raise TypeError("rect command fill must be a Color")
        if self.stroke is not None and stroke is None:
            raise TypeError("rect command stroke must be a Color")
        object.__setattr__(self, "rect", rect)
        object.__setattr__(self, "fill", fill)
        object.__setattr__(self, "stroke", stroke)
        try:
            stroke_width = float(self.stroke_width)
        except (TypeError, ValueError):
            stroke_width = 1.0
        object.__setattr__(self, "stroke_width", max(0.0, stroke_width))
        object.__setattr__(self, "alpha", _normalize_alpha(self.alpha))
        _normalize_order_fields(self, self.layer, self.z, self.order)


@dataclass(frozen=True, slots=True)
class EllipseCommand(RectCommand):
    """One backend-neutral ellipse draw operation."""


@dataclass(frozen=True, slots=True)
class ClipPush:
    """Save painter state and intersect it with ``rect``."""

    rect: Rect

    def __post_init__(self) -> None:
        rect = coerce_rect(self.rect)
        if rect is None:
            raise TypeError("clip rect must be a Rect")
        object.__setattr__(self, "rect", rect)


@dataclass(frozen=True, slots=True)
class ClipPop:
    """Restore the state saved by the corresponding :class:`ClipPush`."""


def _normalize_transform(value: object) -> tuple[float, float, float, float, float, float]:
    if value is None:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not isinstance(value, (tuple, list)) or len(value) != 6:
        raise TypeError("transform matrix must contain six numeric values")
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        raise TypeError("transform matrix must contain six numeric values") from exc


@dataclass(frozen=True, slots=True)
class TransformPush:
    """Save painter state and concatenate a 2D affine transform.

    The matrix uses Qt/Direct2D order ``(m11, m12, m21, m22, dx, dy)``.
    """

    matrix: tuple[float, float, float, float, float, float] = (
        1.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "matrix", _normalize_transform(self.matrix))


@dataclass(frozen=True, slots=True)
class TransformPop:
    """Restore the state saved by the corresponding :class:`TransformPush`."""


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

    commands: tuple[
        SpriteCommand
        | TextCommand
        | LineCommand
        | RectCommand
        | EllipseCommand
        | ClipPush
        | ClipPop
        | TransformPush
        | TransformPop,
        ...
    ] = ()
    resource_revisions: tuple[ResourceRevision, ...] = ()

    def __post_init__(self) -> None:
        commands = tuple(self.commands)
        resource_revisions = tuple(self.resource_revisions)
        command_types = (
            SpriteCommand,
            TextCommand,
            LineCommand,
            RectCommand,
            EllipseCommand,
            ClipPush,
            ClipPop,
            TransformPush,
            TransformPop,
        )
        if any(not isinstance(command, command_types) for command in commands):
            raise TypeError("draw batch contains an unsupported command value")
        if any(
            not isinstance(resource, ResourceRevision)
            for resource in resource_revisions
        ):
            raise TypeError("draw batch resources must be ResourceRevision values")
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "resource_revisions", resource_revisions)


def scale_batch_alpha(batch: DrawBatch, alpha: float) -> DrawBatch:
    """Return a batch whose drawable command alpha is multiplied by ``alpha``."""
    factor = _normalize_alpha(alpha)
    if factor >= 1.0:
        return batch
    commands = tuple(
        replace(command, alpha=command.alpha * factor)
        if hasattr(command, "alpha") else command
        for command in batch.commands
    )
    return DrawBatch(commands, batch.resource_revisions)
