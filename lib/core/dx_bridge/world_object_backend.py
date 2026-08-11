"""DirectX implementation of the backend-neutral world-object facade."""
from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from itertools import count

from config.config import BEHAVIOR, PHYSICS, SNOWBALL
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.image_loader import resize_image_resource
from lib.core.graphics.types import Point, Rect
from lib.core.graphics.visuals import (
    build_world_object_batch,
    resolve_speaker_scale,
    sample_motor_jitter,
    update_speaker_intensity,
)
from lib.core.input.types import MouseButton, MouseButtons
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.physics import PhysicsBody, get_physics_world
from lib.core.world_objects import (
    WorldObjectBackend,
    WorldObjectMotion,
    WorldObjectRequest,
    WorldObjectState,
    normalize_clock_countdown,
    tick_clock_countdown,
)

from .loop import DxLoopContext
from .screen import DxScreenProvider, get_cursor_position
from .window_host import DxWindowHost


_PHYSICS_TYPES = {"motor", "clock", "sofa", "snowball", "snow_leopard", "speaker"}
_FLIPPABLE_TYPES = {"motor", "sofa", "snow_leopard", "speaker"}
_MAX_THROW_VX = float(PHYSICS.get("max_throw_vx", 25.0))
_MAX_THROW_VY = float(PHYSICS.get("max_throw_vy", 25.0))
_DRAG_THRESHOLD = float(PHYSICS.get("drag_threshold", 5))
_MAX_BOUNCES = int(PHYSICS.get("max_bounces", 5))
_GROUND_Y_PCT = float(PHYSICS.get("ground_y_pct", 0.90))
_FADE_STEP = float(PHYSICS.get("fade_step", 0.05))
_DRAG_TRAIL_WINDOW_SECONDS = 0.10


class _DxWorldObject:
    def __init__(
        self,
        instance_id: int,
        request: WorldObjectRequest,
        *,
        context: DxLoopContext,
        screen_provider: DxScreenProvider,
        physics_world: object,
        window_host_factory: Callable[..., DxWindowHost],
        cursor_position_provider: Callable[[], Point],
        warp: bool,
    ) -> None:
        self.instance_id = int(instance_id)
        self.object_type = request.object_type
        self.resource = resize_image_resource(request.resource, request.size)
        self.options = request.option_dict()
        self._context = context
        self._screen_provider = screen_provider
        self._physics_world = physics_world
        self._cursor_position_provider = cursor_position_provider
        self._alive = True
        self._fading = False
        self._flipped = False
        self._frozen = False
        self._alpha = 1.0
        self._frame_index = 0
        self._pending_click = False
        self._pending_click_ticks = 0
        self._double_click_ticks = max(1, int(BEHAVIOR.get("double_click_ticks", 3)))
        self._press_global: Point | None = None
        self._drag_offset: Point | None = None
        self._dragging = False
        self._drag_trail: deque[tuple[float, Point]] = deque()
        self._physics_body: PhysicsBody | None = None
        self._physics_cleaned = False
        self._lifetime_ticks_left: int | None = None
        self._countdown_centis: int | None = None
        self._render_offset = Point()
        self._speaker_intensity = 0.0
        if self.object_type == "clock":
            self._countdown_centis = normalize_clock_countdown(
                self.options.get("countdown_hh", 0),
                self.options.get("countdown_mm", 0),
                self.options.get("countdown_ss", 0),
                self.options.get("countdown_ms", 0),
            )

        width, height = request.size
        self.host = window_host_factory(
            width,
            height,
            x=int(round(request.position.x)),
            y=int(round(request.position.y)),
            callbacks=self,
            warp=warp,
            topmost=True,
            tool_window=True,
            no_activate=False,
            clickthrough=is_clickthrough_enabled(),
        )
        try:
            self._context.register_poller(self.host)
            get_layer_manager().register(
                self.host,
                Layer.WORLD_OBJECT,
                name=f"DxWorldObject:{self.object_type}:{self.instance_id}",
            )
            self._create_physics_body(request.position, width, height)
            if self.object_type == "snowball":
                lifetime = random.uniform(
                    float(SNOWBALL.get("lifetime_min", 10)),
                    float(SNOWBALL.get("lifetime_max", 15)),
                )
                self._lifetime_ticks_left = max(1, round(lifetime * 20.0))
            self.host.show()
            get_layer_manager().enforce_burst()
            self.host.request_repaint()
        except Exception:
            self._cleanup_physics()
            get_layer_manager().unregister(self.host)
            self._context.unregister_poller(self.host)
            self.host.cleanup()
            raise

    def _create_physics_body(self, position: Point, width: int, height: int) -> None:
        if self.object_type not in _PHYSICS_TYPES:
            return
        screen = self._screen_provider.get_screen_rect_for_point(Point(
            position.x + width / 2.0,
            position.y + height / 2.0,
        ))
        body = PhysicsBody(
            float(position.x),
            float(position.y),
            screen.y + screen.height * _GROUND_Y_PCT - height,
            width,
            height,
            _MAX_BOUNCES,
        )
        if self.object_type == "snowball":
            body.bounce_vx_retain = float(SNOWBALL.get("ground_friction", 0.96))
        body.on_position_change = self._on_physics_position_change
        body.on_wall_hit = self._on_physics_wall_hit
        body.on_ground_bounce = self._on_physics_ground_bounce
        self._physics_body = body
        self._physics_world.add_body(body)
        body.active = True

    def prepare_render(self):
        scale_x, scale_y = resolve_speaker_scale(self._speaker_intensity)
        return build_world_object_batch(
            self.resource,
            self._frame_index,
            alpha=self._alpha,
            flipped=self._flipped,
            order=self.instance_id,
            object_type=self.object_type,
            countdown_centis=self._countdown_centis,
            position=self._render_offset,
            scale_x=scale_x if self.object_type == "speaker" else 1.0,
            scale_y=scale_y if self.object_type == "speaker" else 1.0,
        )

    def tick(self) -> None:
        if not self._alive:
            return
        if self._fading:
            self._alpha = max(0.0, self._alpha - _FADE_STEP)
            if self._alpha <= 0.0:
                self.cleanup()
            else:
                self.host.request_repaint()
            return
        if self._pending_click:
            self._pending_click_ticks += 1
            if self._pending_click_ticks >= self._double_click_ticks:
                self._pending_click = False
                self._pending_click_ticks = 0
        if self._countdown_centis is not None and self._countdown_centis > 0:
            self._countdown_centis, text_changed = tick_clock_countdown(
                self._countdown_centis,
            )
            if text_changed:
                self.host.request_repaint()
        if self.object_type == "motor":
            body = self._physics_body
            moving = self._drag_offset is not None or (
                body is not None
                and body.active
                and (abs(body.vx) > 0.01 or abs(body.vy) > 0.01)
            )
            self._render_offset = sample_motor_jitter(moving)
            self.host.request_repaint()
        elif self.object_type == "speaker":
            from lib.core.audio_meter import get_audio_meter

            self._speaker_intensity = update_speaker_intensity(
                self._speaker_intensity,
                get_audio_meter().get_frequency_intensity(),
            )
            self.host.request_repaint()
        if self._lifetime_ticks_left is not None and self._drag_offset is None:
            self._lifetime_ticks_left -= 1
            if self._lifetime_ticks_left <= 0:
                self.start_fadeout()

    def advance_animation(self) -> None:
        if self._alive and len(self.resource.frames) > 1:
            self._frame_index = (self._frame_index + 1) % len(self.resource.frames)
            self.host.request_repaint()

    def _on_physics_position_change(self, body: PhysicsBody) -> None:
        if not self._alive or self._fading or self._drag_offset is not None:
            return
        geometry = self.host.get_geometry()
        self.host.set_geometry(Rect(
            body.render_x,
            body.render_y,
            geometry.width,
            geometry.height,
        ))

    def _on_physics_wall_hit(self, body: PhysicsBody, side: str) -> None:
        if self.object_type in _FLIPPABLE_TYPES:
            self._flipped = side == "left"
            self.host.request_repaint()

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        if self.object_type == "snowball" and stopped:
            self._frozen = True

    def _update_ground(self) -> None:
        body = self._physics_body
        if body is None:
            return
        geometry = self.host.get_geometry()
        screen = self._screen_provider.get_screen_rect_for_point(Point(
            geometry.x + geometry.width / 2.0,
            geometry.y + geometry.height / 2.0,
        ))
        body.ground_y = screen.y + screen.height * _GROUND_Y_PCT - geometry.height

    def _sync_body_to_host(self, *, velocity: Point | None = None, active: bool | None = None) -> None:
        body = self._physics_body
        if body is None:
            return
        geometry = self.host.get_geometry()
        body.invalidate_pending_updates()
        body.x = body.prev_x = body.render_x = float(geometry.x)
        body.y = body.prev_y = body.render_y = float(geometry.y)
        if velocity is not None:
            body.vx = max(-_MAX_THROW_VX, min(_MAX_THROW_VX, velocity.x))
            body.vy = max(-_MAX_THROW_VY, min(_MAX_THROW_VY, velocity.y))
        if active is not None:
            body.active = bool(active)
        self._update_ground()

    def _release_velocity(self, position: Point) -> Point:
        now = time.monotonic()
        self._drag_trail.append((now, position))
        cutoff = now - _DRAG_TRAIL_WINDOW_SECONDS
        while self._drag_trail and self._drag_trail[0][0] < cutoff:
            self._drag_trail.popleft()
        if len(self._drag_trail) < 2:
            return Point()
        start_time, start = self._drag_trail[0]
        elapsed = now - start_time
        if elapsed <= 0.0:
            return Point()
        frames = elapsed * 60.0
        return Point((position.x - start.x) / frames, (position.y - start.y) / frames)

    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        return None

    def handle_pointer_press(self, event: object) -> None:
        if self._fading:
            return
        button = getattr(event, "button", MouseButton.NONE)
        if button == MouseButton.RIGHT:
            self._handle_right_click()
            return
        if button != MouseButton.LEFT:
            return
        if self._pending_click:
            self._pending_click = False
            self._pending_click_ticks = 0
            self.start_fadeout()
            return
        self._pending_click = True
        self._pending_click_ticks = 0
        self._press_global = getattr(event, "global_pos", Point())
        local = getattr(event, "pos", Point())
        self._drag_offset = Point(local.x, local.y)
        self._dragging = False
        self._drag_trail.clear()
        self._drag_trail.append((time.monotonic(), self._press_global))
        self._frozen = False
        if self._physics_body is not None:
            self._physics_body.invalidate_pending_updates()
            self._physics_body.active = False
            self._physics_body.bounce_count = 0

    def _handle_right_click(self) -> None:
        if self.object_type == "snow_pile":
            center = self.center()
            event_center = get_event_center()
            event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                "particle_id": "snow_drift",
                "area_type": "point",
                "area_data": (center.x, center.y),
            }))
            event_center.publish(Event(EventType.MANAGER_INTERACTION, {
                "manager_id": "snow_pile",
                "action": "spawn_leopard",
                "position": center,
            }))
        elif self.object_type in _FLIPPABLE_TYPES:
            self._flipped = not self._flipped
            self.host.request_repaint()

    def handle_pointer_move(self, event: object) -> None:
        buttons = getattr(event, "buttons", MouseButtons.NONE)
        if self._drag_offset is None or not (buttons & MouseButtons.LEFT) or self._fading:
            return
        global_pos = getattr(event, "global_pos", Point())
        if self._press_global is not None and not self._dragging:
            delta_x = global_pos.x - self._press_global.x
            delta_y = global_pos.y - self._press_global.y
            if delta_x * delta_x + delta_y * delta_y < _DRAG_THRESHOLD * _DRAG_THRESHOLD:
                return
            self._dragging = True
            self._pending_click = False
            self._pending_click_ticks = 0
        geometry = self.host.get_geometry()
        x = global_pos.x - self._drag_offset.x
        y = global_pos.y - self._drag_offset.y
        if self.object_type == "snow_pile":
            screen = self._screen_provider.get_screen_rect_for_point(global_pos)
            x = max(screen.x, min(x, screen.x + screen.width - geometry.width))
            y = geometry.y
        self.host.set_geometry(Rect(x, y, geometry.width, geometry.height))
        self._sync_body_to_host(active=False)
        self._drag_trail.append((time.monotonic(), global_pos))

    def handle_pointer_release(self, button: MouseButton) -> None:
        if button != MouseButton.LEFT:
            return
        position = self._cursor_position_provider()
        velocity = self._release_velocity(position) if self._dragging else Point()
        self._drag_offset = None
        self._press_global = None
        self._dragging = False
        self._sync_body_to_host(velocity=velocity, active=self._physics_body is not None)

    def handle_window_moved(self, position: Point) -> None:
        return None

    def handle_key_press(self, event: object) -> None:
        return None

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_host_close(self) -> None:
        self.cleanup()

    def set_clickthrough(self, enabled: bool) -> None:
        if self._alive:
            self.host.set_clickthrough(enabled)

    def set_gravity_enabled(self, enabled: bool) -> None:
        body = self._physics_body
        if body is not None:
            body.gravity_enabled = bool(enabled)

    def start_fadeout(self) -> None:
        if not self._alive or self._fading:
            return
        self._fading = True
        self._drag_offset = None
        self._press_global = None
        self._dragging = False
        self._cleanup_physics()

    def spawn_jump(self, power_min: float, power_max: float) -> None:
        body = self._physics_body
        if body is None or self._fading:
            return
        low, high = sorted((float(power_min), float(power_max)))
        power = random.uniform(low, high)
        self._flipped = random.choice((True, False))
        body.invalidate_pending_updates()
        body.vx = (5.0 if self._flipped else -5.0) * power
        body.vy = -13.0 * power
        body.bounce_count = 0
        body.active = True
        self._frozen = False
        self.host.request_repaint()

    def state(self) -> WorldObjectState:
        return WorldObjectState(
            alive=self._alive,
            fading=self._fading,
            flipped=self._flipped,
            dragging=self._dragging,
            frozen=self._frozen,
        )

    def motion(self) -> WorldObjectMotion | None:
        body = self._physics_body
        if body is None or self.object_type != "snowball":
            return None
        return WorldObjectMotion(
            Point(body.x, body.y),
            Point(body.vx, body.vy),
            max(1.0, body.width / 2.0),
        )

    def geometry(self) -> Rect:
        return self.host.get_geometry() if self._alive else Rect()

    def center(self) -> Point:
        geometry = self.geometry()
        return Point(
            geometry.x + geometry.width / 2.0,
            geometry.y + geometry.height / 2.0,
        )

    def apply_motion_delta(self, position: Point, velocity: Point | None, wake: bool) -> None:
        body = self._physics_body
        if body is None:
            return
        body.invalidate_pending_updates()
        body.x += float(position.x)
        body.y += float(position.y)
        body.prev_x = body.render_x = body.x
        body.prev_y = body.render_y = body.y
        if velocity is not None:
            body.vx += float(velocity.x)
            body.vy += float(velocity.y)
        if wake:
            self._frozen = False
            body.active = True
            body.bounce_count = 0
        self._on_physics_position_change(body)

    def _cleanup_physics(self) -> None:
        if self._physics_cleaned:
            return
        self._physics_cleaned = True
        body = self._physics_body
        if body is not None:
            body.active = False
            self._physics_world.remove_body(body)

    def cleanup(self) -> None:
        if not self._alive:
            return
        self._alive = False
        self._cleanup_physics()
        try:
            get_layer_manager().unregister(self.host)
        finally:
            self._context.unregister_poller(self.host)
        self.host.cleanup()


class DxWorldObjectBackend(WorldObjectBackend):
    """Host all supported world-object requests in native DX windows."""

    backend_id = "directx"

    def __init__(
        self,
        context: DxLoopContext,
        *,
        screen_provider: DxScreenProvider | None = None,
        physics_world: object | None = None,
        window_host_factory: Callable[..., DxWindowHost] | None = None,
        cursor_position_provider: Callable[[], Point] | None = None,
        warp: bool = False,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider or DxScreenProvider()
        self._physics_world = physics_world or get_physics_world()
        self._window_host_factory = window_host_factory or DxWindowHost
        self._cursor_position_provider = cursor_position_provider or get_cursor_position
        self._warp = bool(warp)
        self._next_id = count(1)
        self._instances: dict[int, _DxWorldObject] = {}
        self._event_center = get_event_center()
        self._cleanup_done = False
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.GIF_FRAME, self._on_gif_frame)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _get(self, instance_id: int) -> _DxWorldObject | None:
        instance = self._instances.get(int(instance_id))
        if instance is not None and not instance._alive:
            self._instances.pop(int(instance_id), None)
            return None
        return instance

    def create(self, request: WorldObjectRequest) -> int:
        if self._cleanup_done:
            raise RuntimeError("DX world-object backend has been cleaned")
        instance_id = next(self._next_id)
        instance = _DxWorldObject(
            instance_id,
            request,
            context=self._context,
            screen_provider=self._screen_provider,
            physics_world=self._physics_world,
            window_host_factory=self._window_host_factory,
            cursor_position_provider=self._cursor_position_provider,
            warp=self._warp,
        )
        self._instances[instance_id] = instance
        return instance_id

    def _on_tick(self, event: Event) -> None:
        for instance_id, instance in tuple(self._instances.items()):
            instance.tick()
            if not instance._alive:
                self._instances.pop(instance_id, None)

    def _on_gif_frame(self, event: Event) -> None:
        for instance in tuple(self._instances.values()):
            instance.advance_animation()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get("enabled", False))
        for instance in tuple(self._instances.values()):
            instance.set_clickthrough(enabled)

    def get_state(self, instance_id: int) -> WorldObjectState:
        instance = self._get(instance_id)
        return WorldObjectState(alive=False) if instance is None else instance.state()

    def get_motion(self, instance_id: int) -> WorldObjectMotion | None:
        instance = self._get(instance_id)
        return None if instance is None else instance.motion()

    def apply_motion_delta(
        self,
        instance_id: int,
        *,
        position: Point,
        velocity: Point | None,
        wake: bool,
    ) -> None:
        instance = self._get(instance_id)
        if instance is not None:
            instance.apply_motion_delta(position, velocity, wake)

    def set_gravity_enabled(self, instance_id: int, enabled: bool) -> None:
        instance = self._get(instance_id)
        if instance is not None:
            instance.set_gravity_enabled(enabled)

    def start_fadeout(self, instance_id: int) -> None:
        instance = self._get(instance_id)
        if instance is not None:
            instance.start_fadeout()

    def spawn_jump(self, instance_id: int, power_min: float, power_max: float) -> None:
        instance = self._get(instance_id)
        if instance is not None:
            instance.spawn_jump(power_min, power_max)

    def close(self, instance_id: int) -> None:
        instance = self._instances.pop(int(instance_id), None)
        if instance is not None:
            instance.cleanup()

    def get_center(self, instance_id: int) -> Point:
        instance = self._get(instance_id)
        return Point() if instance is None else instance.center()

    def get_geometry(self, instance_id: int) -> Rect:
        instance = self._get(instance_id)
        return Rect() if instance is None else instance.geometry()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.GIF_FRAME, self._on_gif_frame)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        for instance in tuple(self._instances.values()):
            instance.cleanup()
        self._instances.clear()


__all__ = ["DxWorldObjectBackend"]
