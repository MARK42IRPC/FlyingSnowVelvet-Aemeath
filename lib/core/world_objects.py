"""Backend-neutral world-object resources, instances, and host protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lib.core.graphics.resources import ImageResource
from lib.core.graphics.types import Point, Rect, coerce_point


_CLOCK_MAX_COUNTDOWN_CENTIS = ((99 * 60 + 59) * 60 + 59) * 100 + 99
_CLOCK_CENTIS_PER_TICK = 5


def normalize_clock_countdown(hh: object = 0, mm: object = 0, ss: object = 0, ms: object = 0) -> int:
    """Normalize the shared clock countdown representation to centiseconds."""
    try:
        hours = max(0, min(99, int(hh)))
    except (TypeError, ValueError):
        hours = 0
    try:
        minutes = max(0, min(59, int(mm)))
    except (TypeError, ValueError):
        minutes = 0
    try:
        seconds = max(0, min(59, int(ss)))
    except (TypeError, ValueError):
        seconds = 0
    try:
        centis = max(0, min(99, int(ms)))
    except (TypeError, ValueError):
        centis = 0
    return min(_CLOCK_MAX_COUNTDOWN_CENTIS, ((hours * 60 + minutes) * 60 + seconds) * 100 + centis)


def tick_clock_countdown(centis: object, *, step: int = _CLOCK_CENTIS_PER_TICK) -> tuple[int, bool]:
    """Advance a countdown and report whether its displayed text changed."""
    try:
        previous = max(0, int(centis))
    except (TypeError, ValueError):
        previous = 0
    current = max(0, previous - max(1, int(step)))
    return current, format_clock_countdown(previous) != format_clock_countdown(current)


def clock_countdown_parts(centis: object) -> tuple[int, int, int, int]:
    try:
        total = max(0, int(centis))
    except (TypeError, ValueError):
        total = 0
    hours = total // 360000
    total %= 360000
    minutes = total // 6000
    total %= 6000
    seconds = total // 100
    return hours, minutes, seconds, total % 100


def whole_clock_seconds(centis: object) -> int:
    try:
        value = max(0, int(centis))
    except (TypeError, ValueError):
        value = 0
    return 0 if value == 0 else (value + 99) // 100


def format_clock_countdown(centis: object) -> str:
    hours, minutes, seconds, millis = clock_countdown_parts(centis)
    if hours > 0:
        left, right = hours, minutes
    elif minutes > 0:
        left, right = minutes, seconds
    else:
        left, right = seconds, millis
    return f"{left:02d}:{right:02d}"


def _is_world_object_option(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    return isinstance(value, tuple) and all(
        _is_world_object_option(item) for item in value
    )


@dataclass(frozen=True, slots=True)
class WorldObjectRequest:
    """Immutable construction data submitted to a desktop backend."""

    object_type: str
    resource: ImageResource
    position: Point
    size: tuple[int, int]
    options: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        object_type = str(self.object_type or "").strip()
        if not object_type:
            raise ValueError("world-object type must not be empty")
        if not isinstance(self.resource, ImageResource):
            raise TypeError("world-object resource must be an ImageResource")
        position = coerce_point(self.position)
        if position is None:
            raise TypeError("world-object position must be a Point")
        size = (int(self.size[0]), int(self.size[1]))
        if size[0] <= 0 or size[1] <= 0:
            raise ValueError("world-object dimensions must be positive")
        options = tuple((str(key), value) for key, value in self.options)
        option_names = [key for key, _value in options]
        if any(not key for key in option_names):
            raise ValueError("world-object option names must not be empty")
        if len(set(option_names)) != len(option_names):
            raise ValueError("world-object option names must be unique")
        invalid_options = [
            key for key, value in options if not _is_world_object_option(value)
        ]
        if invalid_options:
            raise TypeError(
                "world-object options must contain only scalar or tuple values: "
                + ", ".join(invalid_options)
            )
        object.__setattr__(self, "object_type", object_type)
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "options", options)

    def option_dict(self) -> dict[str, object]:
        return dict(self.options)


@dataclass(frozen=True, slots=True)
class WorldObjectState:
    """Backend-neutral lifecycle and interaction state for one instance."""

    alive: bool
    fading: bool = False
    flipped: bool = False
    dragging: bool = False
    frozen: bool = False


@dataclass(frozen=True, slots=True)
class WorldObjectMotion:
    """Physics state needed by backend-neutral world-object interactions."""

    position: Point
    velocity: Point
    radius: float

    def __post_init__(self) -> None:
        if not isinstance(self.position, Point) or not isinstance(self.velocity, Point):
            raise TypeError("world-object motion requires Point values")
        if float(self.radius) <= 0:
            raise ValueError("world-object motion radius must be positive")
        object.__setattr__(self, "radius", float(self.radius))


@dataclass(frozen=True, slots=True)
class WorldObjectInstance:
    """Stable instance handle that never exposes a native window object."""

    backend_id: str
    instance_id: int
    object_type: str

    def __post_init__(self) -> None:
        backend_id = str(self.backend_id or "").strip()
        object_type = str(self.object_type or "").strip()
        instance_id = int(self.instance_id)
        if not backend_id:
            raise ValueError("world-object backend id must not be empty")
        if not object_type:
            raise ValueError("world-object type must not be empty")
        if instance_id <= 0:
            raise ValueError("world-object instance id must be positive")
        object.__setattr__(self, "backend_id", backend_id)
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "object_type", object_type)

    def get_state(self) -> WorldObjectState:
        return _require_instance_backend(self).get_state(self.instance_id)

    def get_motion(self) -> WorldObjectMotion | None:
        return _require_instance_backend(self).get_motion(self.instance_id)

    def get_center(self) -> Point:
        return _require_instance_backend(self).get_center(self.instance_id)

    def get_geometry(self) -> Rect:
        return _require_instance_backend(self).get_geometry(self.instance_id)

    def is_alive(self) -> bool:
        return self.get_state().alive

    def set_gravity_enabled(self, enabled: bool) -> None:
        _require_instance_backend(self).set_gravity_enabled(
            self.instance_id,
            bool(enabled),
        )

    def start_fadeout(self) -> None:
        _require_instance_backend(self).start_fadeout(self.instance_id)

    def spawn_jump(self, power_min: float, power_max: float) -> None:
        _require_instance_backend(self).spawn_jump(
            self.instance_id,
            float(power_min),
            float(power_max),
        )

    def apply_motion_delta(
        self,
        *,
        position: Point,
        velocity: Point | None = None,
        wake: bool = False,
    ) -> None:
        _require_instance_backend(self).apply_motion_delta(
            self.instance_id,
            position=position,
            velocity=velocity,
            wake=bool(wake),
        )

    def close(self) -> None:
        _require_instance_backend(self).close(self.instance_id)


class WorldObjectBackend(Protocol):
    backend_id: str

    def create(self, request: WorldObjectRequest) -> int: ...

    def get_state(self, instance_id: int) -> WorldObjectState: ...

    def get_motion(self, instance_id: int) -> WorldObjectMotion | None: ...

    def apply_motion_delta(
        self,
        instance_id: int,
        *,
        position: Point,
        velocity: Point | None,
        wake: bool,
    ) -> None: ...

    def set_gravity_enabled(self, instance_id: int, enabled: bool) -> None: ...

    def start_fadeout(self, instance_id: int) -> None: ...

    def spawn_jump(
        self,
        instance_id: int,
        power_min: float,
        power_max: float,
    ) -> None: ...

    def close(self, instance_id: int) -> None: ...

    def get_center(self, instance_id: int) -> Point: ...

    def get_geometry(self, instance_id: int) -> Rect: ...


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


def _require_instance_backend(instance: WorldObjectInstance) -> WorldObjectBackend:
    backend = _require_backend()
    if backend.backend_id != instance.backend_id:
        raise RuntimeError(
            f"world-object instance belongs to backend '{instance.backend_id}', "
            f"not '{backend.backend_id}'"
        )
    return backend


def create_world_object(
    object_type: str,
    *,
    resource: ImageResource,
    position: Point,
    size: tuple[int, int],
    **options: object,
) -> WorldObjectInstance:
    request = WorldObjectRequest(
        object_type=object_type,
        resource=resource,
        position=position,
        size=size,
        options=tuple(options.items()),
    )
    backend = _require_backend()
    instance_id = backend.create(request)
    return WorldObjectInstance(backend.backend_id, instance_id, request.object_type)


def get_world_object_center(instance: WorldObjectInstance) -> Point:
    if not isinstance(instance, WorldObjectInstance):
        raise TypeError("world-object center requires a WorldObjectInstance")
    return instance.get_center()


def get_world_object_geometry(instance: WorldObjectInstance) -> Rect:
    if not isinstance(instance, WorldObjectInstance):
        raise TypeError("world-object geometry requires a WorldObjectInstance")
    return instance.get_geometry()
