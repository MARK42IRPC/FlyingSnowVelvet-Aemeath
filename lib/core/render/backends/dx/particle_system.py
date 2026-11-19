"""Qt-free particle controller and declaration-based DX renderer."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.services.feedback_scene import (
    create_particle_instances, instance_alive, interpolate_particles,
    prepare_particles, update_instances,
)
from lib.core.render.visuals.commands import DrawBatch
from lib.core.render.visuals.visuals import build_particle_batch as _build_particle_batch
from lib.core.render.layers import Layer, normalize_layer
from lib.core.logger import get_logger

from .loop import DxLoopContext
from .overlay_window import DxOverlayWindow
from .screen import DxScreenProvider


_logger = get_logger(__name__)


def _particle_alive(particle: object) -> bool:
    return instance_alive(particle)


def build_particle_batch(particles: list[object]) -> DrawBatch:
    """Compatibility export for the shared particle presenter."""
    return _build_particle_batch(particles)


class DxParticleOverlay:
    """Event-driven particle state with a declaration-only DX render edge."""

    def __init__(
        self,
        context: DxLoopContext,
        *,
        screen_provider: DxScreenProvider | None = None,
        window: DxOverlayWindow | None = None,
        warp: bool = False,
        particle_manager=None,
    ) -> None:
        self._event_center = get_event_center()
        if particle_manager is None:
            raise ValueError("DX particle manager is required")
        self._particle_manager = particle_manager
        self._window = window or DxOverlayWindow(
            context,
            Layer.PARTICLE,
            name="DxParticleOverlay",
            screen_provider=screen_provider,
            warp=warp,
        )
        self._particles: list[object] = []
        self._pending_requests: deque[dict] = deque()
        self._paused = False
        self._cleanup_done = False
        self._draw_seq = 0
        self._event_center.subscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    @property
    def window_host(self):
        return self._window.window_host

    def _on_particle_request(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        particle_id = data.get("particle_id")
        area_data = data.get("area_data")
        if not particle_id or not area_data or self._paused:
            event.mark_handled()
            return
        self._pending_requests.append({
            "particle_id": str(particle_id),
            "area_type": data.get("area_type", "point"),
            "area_data": area_data,
            "particle_options": dict(data.get("particle_options") or {}),
        })
        event.mark_handled()

    def _on_tick(self, event: Event) -> None:
        if self._paused or self._cleanup_done:
            return
        self._drain_requests()
        prepare_particles(self._particles)
        self._particles = update_instances(self._particles)
        if not self._particles:
            self._window.flush_immediately()

    def _on_frame(self, event: Event) -> None:
        if self._paused or self._cleanup_done or not self._particles:
            return
        data = event.data if isinstance(event.data, dict) else {}
        alpha = max(0.0, min(1.0, float(data.get("tick_alpha", 1.0) or 0.0)))
        interpolate_particles(self._particles, alpha)
        self._window.submit(build_particle_batch(self._particles))

    def _drain_requests(self) -> None:
        if not self._pending_requests:
            return
        geometry = self._window.geometry
        offset_x, offset_y = geometry.x, geometry.y
        while self._pending_requests:
            request = self._pending_requests.popleft()
            options = request["particle_options"]
            area_type = request["area_type"]
            values = request["area_data"]
            try:
                particles = create_particle_instances(
                    self._particle_manager, request['particle_id'], area_type, values, options,
                    offset_x=offset_x, offset_y=offset_y,
                )
            except Exception:
                _logger.exception("DX particle creation failed for %s", request["particle_id"])
                continue
            for particle in particles or ():
                self._draw_seq += 1
                particle.layer = normalize_layer(options.get("layer", getattr(particle, "layer", Layer.PARTICLE)), Layer.PARTICLE)
                try:
                    particle.z = int(options.get("z", getattr(particle, "z", 0)))
                except (TypeError, ValueError):
                    particle.z = 0
                particle._draw_order = self._draw_seq
                particle._tick_prev_x = float(getattr(particle, "x", 0.0))
                particle._tick_prev_y = float(getattr(particle, "y", 0.0))
                particle._render_x = particle._tick_prev_x
                particle._render_y = particle._tick_prev_y
                self._particles.append(particle)

    def flush_immediately(self) -> None:
        self._pending_requests.clear()
        self._particles.clear()
        self._window.flush_immediately()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if self._paused:
            self.flush_immediately()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._event_center.unsubscribe(EventType.PARTICLE_REQUEST, self._on_particle_request)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self.flush_immediately()
        self._window.cleanup()


def create_particle_overlay_factory(
    context: DxLoopContext,
    *,
    screen_provider: DxScreenProvider | None = None,
    warp: bool = False,
    particle_manager_provider=None,
) -> Callable[[], DxParticleOverlay]:
    def create() -> DxParticleOverlay:
        if particle_manager_provider is None:
            raise ValueError("DX particle manager provider is required")
        return DxParticleOverlay(
            context,
            screen_provider=screen_provider,
            warp=warp,
            particle_manager=particle_manager_provider(),
        )

    return create


__all__ = ["DxParticleOverlay", "build_particle_batch", "create_particle_overlay_factory"]
