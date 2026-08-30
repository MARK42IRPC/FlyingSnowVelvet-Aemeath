"""Qt-free effect controller backed by declaration-only DX drawing."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from pathlib import Path

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.commands import DrawBatch
from lib.core.graphics.resources import ImageResource
from lib.core.graphics.visuals import build_effect_batch as _build_effect_batch, load_effect_resource
from lib.core.layer import Layer
from lib.core.logger import get_logger

from .loop import DxLoopContext
from .overlay_window import DxOverlayWindow
from .screen import DxScreenProvider


_logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _effect_alive(effect: object) -> bool:
    alive = getattr(effect, "alive", True)
    try:
        return bool(alive() if callable(alive) else alive)
    except Exception:
        return False


def _resolve_path(value: object) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def build_effect_batch(effects: list[object]) -> DrawBatch:
    """Compatibility export for the shared effect presenter."""
    return _build_effect_batch(effects)


class DxEffectOverlay:
    """Event-driven effect state with WIC/Pillow resources at the DX edge."""

    def __init__(
        self,
        context: DxLoopContext,
        *,
        screen_provider: DxScreenProvider | None = None,
        window: DxOverlayWindow | None = None,
        warp: bool = False,
        effect_manager=None,
    ) -> None:
        self._event_center = get_event_center()
        if effect_manager is None:
            raise ValueError("DX effect manager is required")
        self._effect_manager = effect_manager
        self._window = window or DxOverlayWindow(
            context,
            Layer.EFFECT,
            name="DxEffectOverlay",
            screen_provider=screen_provider,
            warp=warp,
        )
        self._effects: list[object] = []
        self._resources: dict[tuple[object, ...], ImageResource] = {}
        self._pending_requests: deque[dict] = deque()
        self._paused = False
        self._cleanup_done = False
        self._draw_seq = 0
        self._event_center.subscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

    @property
    def window_host(self):
        return self._window.window_host

    def _on_effect_request(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        effect_id = data.get("effect_id")
        if not effect_id or self._paused:
            event.mark_handled()
            return
        options = dict(data.get("effect_options") or {})
        options.pop("pixmap", None)
        self._pending_requests.append({
            "effect_id": str(effect_id),
            "anchor_type": data.get("anchor_type", "point"),
            "anchor_data": data.get("anchor_data"),
            "effect_options": options,
        })
        event.mark_handled()

    def _on_tick(self, event: Event) -> None:
        if self._paused or self._cleanup_done:
            return
        self._drain_requests()
        alive = []
        for effect in self._effects:
            effect._tick_prev_age = float(getattr(effect, "age", 0.0))
            effect._tick_prev_x = float(getattr(effect, "_render_x", getattr(effect, "x", 0.0)))
            effect._tick_prev_y = float(getattr(effect, "_render_y", getattr(effect, "y", 0.0)))
            effect._tick_prev_opacity = float(getattr(
                effect, "_render_opacity", getattr(effect, "opacity", 1.0)
            ))
            effect._tick_prev_scale = float(getattr(
                effect, "_render_scale", getattr(effect, "scale", 1.0)
            ))
            effect._tick_prev_rotation = float(getattr(
                effect, "_render_rotation", getattr(effect, "rotation", 0.0)
            ))
            try:
                effect.update()
            except Exception:
                continue
            if _effect_alive(effect):
                alive.append(effect)
        self._effects = alive
        if not self._effects:
            self._window.flush_immediately()

    def _on_frame(self, event: Event) -> None:
        if self._paused or self._cleanup_done or not self._effects:
            return
        data = event.data if isinstance(event.data, dict) else {}
        alpha = max(0.0, min(1.0, float(data.get("tick_alpha", 1.0) or 0.0)))
        for effect in self._effects:
            apply_interpolation = getattr(effect, "apply_frame_interpolation", None)
            if callable(apply_interpolation):
                try:
                    apply_interpolation(alpha)
                    continue
                except Exception:
                    pass
            for field in ("x", "y", "opacity", "scale", "rotation"):
                previous = float(getattr(
                    effect,
                    f"_tick_prev_{field}",
                    getattr(effect, field, 0.0),
                ))
                current = float(getattr(effect, field, previous))
                setattr(effect, f"_render_{field}", previous + (current - previous) * alpha)
        self._window.submit(build_effect_batch(self._effects))

    def _drain_requests(self) -> None:
        if not self._pending_requests:
            return
        geometry = self._window.geometry
        offset_x, offset_y = geometry.x, geometry.y
        while self._pending_requests:
            request = self._pending_requests.popleft()
            options = dict(request["effect_options"])
            script = self._effect_manager.get_script(request["effect_id"])
            if script is None:
                continue
            path_value = options.get("resource_path")
            resource = None
            if path_value:
                path = _resolve_path(path_value)
                options["resolved_resource_path"] = str(path)
                output_size = options.get("masked_output_size")
                if isinstance(output_size, list):
                    output_size = tuple(output_size)
                cache_key = (
                    str(path),
                    output_size,
                    bool(options.get("edge_feather", False)),
                    options.get("feather_ratio", 0.12),
                )
                resource = self._resources.get(cache_key)
                if resource is None:
                    resource = load_effect_resource(path, options)
                    if resource is not None:
                        self._resources[cache_key] = resource
                if resource is None:
                    _logger.warning("DX effect resource load failed: %s", path)
                    continue
            anchor_type = request["anchor_type"]
            anchor_data = request["anchor_data"]
            try:
                if anchor_type == "rect" and len(anchor_data) >= 4:
                    local_anchor = tuple(float(v) for v in anchor_data[:4])
                    local_anchor = (local_anchor[0] - offset_x, local_anchor[1] - offset_y, local_anchor[2] - offset_x, local_anchor[3] - offset_y)
                elif anchor_type == "circle" and len(anchor_data) >= 3:
                    local_anchor = (float(anchor_data[0]) - offset_x, float(anchor_data[1]) - offset_y, float(anchor_data[2]))
                elif anchor_type == "point" and len(anchor_data) >= 2:
                    local_anchor = (float(anchor_data[0]) - offset_x, float(anchor_data[1]) - offset_y)
                else:
                    local_anchor = anchor_data
                effects = script.create_effects(
                    anchor_type=anchor_type,
                    anchor_data=local_anchor,
                    effect_options=options,
                    request_context={"offset_x": offset_x, "offset_y": offset_y, "project_root": str(_PROJECT_ROOT)},
                )
            except Exception:
                _logger.exception("DX effect creation failed for %s", request["effect_id"])
                continue
            for effect in effects or ():
                if resource is not None:
                    effect._visual_resource = resource
                if not resource and not getattr(effect, "text", ""):
                    continue
                self._draw_seq += 1
                effect.layer = int(Layer.EFFECT)
                try:
                    effect.z = int(options.get("z", getattr(effect, "z", 0)))
                except (TypeError, ValueError):
                    effect.z = 0
                effect._draw_order = self._draw_seq
                effect._render_x = float(getattr(effect, "x", 0.0))
                effect._render_y = float(getattr(effect, "y", 0.0))
                effect._render_opacity = float(getattr(effect, "opacity", 1.0))
                effect._render_scale = float(getattr(effect, "scale", 1.0))
                effect._render_rotation = float(getattr(effect, "rotation", 0.0))
                self._effects.append(effect)

    def flush_immediately(self) -> None:
        self._pending_requests.clear()
        self._effects.clear()
        self._window.flush_immediately()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if self._paused:
            self.flush_immediately()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._event_center.unsubscribe(EventType.EFFECT_REQUEST, self._on_effect_request)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self.flush_immediately()
        self._resources.clear()
        self._window.cleanup()


def create_effect_overlay_factory(
    context: DxLoopContext,
    *,
    screen_provider: DxScreenProvider | None = None,
    warp: bool = False,
    effect_manager_provider=None,
) -> Callable[[], DxEffectOverlay]:
    def create() -> DxEffectOverlay:
        if effect_manager_provider is None:
            raise ValueError("DX effect manager provider is required")
        return DxEffectOverlay(
            context,
            screen_provider=screen_provider,
            warp=warp,
            effect_manager=effect_manager_provider(),
        )

    return create


__all__ = ["DxEffectOverlay", "build_effect_batch", "create_effect_overlay_factory"]
