"""Qt implementation of the backend-neutral world-object protocol."""
from __future__ import annotations

from itertools import count

from lib.core.graphics.types import Point, Rect
from lib.core.qt_bridge import world_object_assets
from lib.core.qt_bridge.world_object_factory import create_world_object
from lib.core.world_objects import (
    WorldObjectBackend,
    WorldObjectMotion,
    WorldObjectRequest,
    WorldObjectState,
)


_WORLD_OBJECT_TYPES = {
    "motor": ("lib.core.qt_bridge.world_objects.motor", "Mortor"),
    "clock": ("lib.core.qt_bridge.world_objects.clock", "Clock"),
    "sofa": ("lib.core.qt_bridge.world_objects.sofa", "Sofa"),
    "snow_pile": ("lib.core.qt_bridge.world_objects.snow_pile", "SnowPile"),
    "snowball": ("lib.core.qt_bridge.world_objects.snowball", "Snowball"),
    "snow_leopard": ("lib.core.qt_bridge.world_objects.snow_leopard", "SnowLeopard"),
    "speaker": ("lib.core.qt_bridge.world_objects.speaker", "Speaker"),
}

_MIRRORED_OBJECT_TYPES = {"motor", "sofa", "speaker"}


class QtWorldObjectBackend(WorldObjectBackend):
    """Own native QWidget instances behind integer process-local handles."""

    backend_id = "qt"

    def __init__(self) -> None:
        self._next_id = count(1)
        self._instances: dict[int, object] = {}

    def _get(self, instance_id: int) -> object | None:
        return self._instances.get(int(instance_id))

    def create(self, request: WorldObjectRequest) -> int:
        try:
            module_name, class_name = _WORLD_OBJECT_TYPES[request.object_type]
        except KeyError as exc:
            raise ValueError(f"unknown world-object type: {request.object_type}") from exc

        instance_id = next(self._next_id)
        options = request.option_dict()
        if request.object_type == "snow_leopard":
            frames, flipped_frames = world_object_assets.image_frame_pair_from_resource(
                request.resource
            )
            options["frames"] = frames
            options["flipped_frames"] = flipped_frames
        else:
            pair = world_object_assets.pixmap_pair_from_resource(request.resource)
            options["pixmap"] = pair.pixmap
            if request.object_type in _MIRRORED_OBJECT_TYPES:
                options["flipped_pixmap"] = pair.flipped_pixmap

        native = create_world_object(
            module_name,
            class_name,
            position=request.position,
            size=request.size,
            **options,
        )
        self._instances[instance_id] = native
        return instance_id

    def get_state(self, instance_id: int) -> WorldObjectState:
        native = self._get(instance_id)
        if native is None:
            return WorldObjectState(alive=False)
        try:
            alive = bool(native.is_alive())
        except (AttributeError, RuntimeError):
            alive = False
        if not alive:
            self._instances.pop(int(instance_id), None)
        return WorldObjectState(
            alive=alive,
            fading=bool(getattr(native, "_fading", False)),
            flipped=bool(getattr(native, "_flipped", False)),
            dragging=getattr(native, "_drag_offset", None) is not None,
            frozen=bool(getattr(native, "_frozen", False)),
        )

    def get_motion(self, instance_id: int) -> WorldObjectMotion | None:
        native = self._get(instance_id)
        if native is None:
            return None
        try:
            body = native.physics_body
            radius = float(native.radius)
        except AttributeError:
            return None
        return WorldObjectMotion(
            position=Point(float(body.x), float(body.y)),
            velocity=Point(float(body.vx), float(body.vy)),
            radius=radius,
        )

    def apply_motion_delta(
        self,
        instance_id: int,
        *,
        position: Point,
        velocity: Point | None,
        wake: bool,
    ) -> None:
        native = self._get(instance_id)
        if native is None:
            return
        try:
            body = native.physics_body
        except AttributeError:
            return
        body.x += float(position.x)
        body.y += float(position.y)
        if body.on_position_change is not None:
            body.on_position_change(body)
        if velocity is None:
            return
        body.vx += float(velocity.x)
        body.vy += float(velocity.y)
        if not wake:
            return
        if bool(getattr(native, "_frozen", False)) and hasattr(native, "unfreeze"):
            native.unfreeze()
        else:
            body.active = True
            body.bounce_count = 0

    def set_gravity_enabled(self, instance_id: int, enabled: bool) -> None:
        native = self._get(instance_id)
        if native is not None and hasattr(native, "set_gravity_enabled"):
            native.set_gravity_enabled(enabled)

    def start_fadeout(self, instance_id: int) -> None:
        native = self._get(instance_id)
        if native is not None and hasattr(native, "start_fadeout"):
            native.start_fadeout()

    def spawn_jump(self, instance_id: int, power_min: float, power_max: float) -> None:
        native = self._get(instance_id)
        if native is not None and hasattr(native, "spawn_jump"):
            native.spawn_jump(power_min, power_max)

    def close(self, instance_id: int) -> None:
        native = self._instances.pop(int(instance_id), None)
        if native is not None and hasattr(native, "close"):
            native.close()

    def get_center(self, instance_id: int) -> Point:
        native = self._get(instance_id)
        if native is None:
            return Point()
        center = native.get_center()
        return Point(float(center.x()), float(center.y()))

    def get_geometry(self, instance_id: int) -> Rect:
        native = self._get(instance_id)
        if native is None:
            return Rect()
        geometry = native.geometry()
        return Rect(
            float(geometry.x()),
            float(geometry.y()),
            float(geometry.width()),
            float(geometry.height()),
        )
