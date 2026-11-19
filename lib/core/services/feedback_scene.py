"""Shared particle/effect instance operations for overlays and embedded scenes.

Scripts and render composers remain the desktop pet's existing implementations.
This module only adapts their clocks, coordinates and ownership to a host.
Managers are injected so core never imports product scripts or GUI backends.
"""
from __future__ import annotations

import math

from lib.core.render.layers import Layer, normalize_layer
from lib.core.render.visuals.commands import DrawBatch
from lib.core.render.visuals.visuals import build_effect_batch, build_particle_batch, load_effect_resource


def instance_alive(instance: object) -> bool:
    try:
        alive = getattr(instance, 'alive', True)
        return bool(alive() if callable(alive) else alive)
    except Exception:
        return False


def local_anchor(kind: str, data, offset_x: float = 0, offset_y: float = 0):
    if data is None:
        return None
    if kind == 'rect':
        x1, y1, x2, y2 = data
        return x1 - offset_x, y1 - offset_y, x2 - offset_x, y2 - offset_y
    if kind == 'circle':
        x, y, radius = data
        return x - offset_x, y - offset_y, radius
    if kind == 'point':
        x, y = data
        return x - offset_x, y - offset_y
    return data


def create_particle_instances(manager, particle_id: str, area_type: str, area_data,
                              options=None, *, offset_x: float = 0, offset_y: float = 0) -> list:
    script = manager.get_script(particle_id) if manager is not None else None
    if script is None:
        return []
    values = dict(options or {})
    # floating_text declares absolute destination coordinates, just like its
    # spawn anchor. Translate both, including on negative-origin desktops.
    for key, offset in (('target_x', offset_x), ('target_y', offset_y)):
        if values.get(key) is not None:
            values[key] = float(values[key]) - offset
    setter = getattr(script, 'set_request_options', None)
    if callable(setter):
        try:
            setter(values)
        except Exception:
            pass
    try:
        return list(script.create_particles(area_type, local_anchor(area_type, area_data, offset_x, offset_y)) or ())
    finally:
        if callable(setter):
            try:
                setter({})
            except Exception:
                pass


def create_effect_instances(manager, effect_id: str, anchor_type: str, anchor_data,
                            options=None, *, request_context=None) -> list:
    script = manager.get_script(effect_id) if manager is not None else None
    if script is None:
        return []
    context = dict(request_context or {})
    anchor = local_anchor(anchor_type, anchor_data, context.get('offset_x', 0), context.get('offset_y', 0))
    return list(script.create_effects(anchor_type=anchor_type, anchor_data=anchor,
                                    effect_options=dict(options or {}), request_context=context) or ())


def prepare_particles(instances) -> None:
    for item in instances:
        item._tick_prev_x = float(getattr(item, 'x', 0.0))
        item._tick_prev_y = float(getattr(item, 'y', 0.0))
        if hasattr(item, 'rotation'):
            item._tick_prev_rotation = float(item.rotation)


def prepare_effects(instances) -> None:
    for item in instances:
        item._tick_prev_age = float(getattr(item, 'age', 0.0))
        for field, default in (('x', 0), ('y', 0), ('opacity', 1), ('scale', 1), ('rotation', 0)):
            setattr(item, f'_tick_prev_{field}', float(getattr(item, f'_render_{field}', getattr(item, field, default))))


def update_instances(instances) -> list:
    alive = []
    for item in instances:
        try:
            item.update()
        except Exception:
            continue
        if instance_alive(item):
            alive.append(item)
    return alive


def interpolate_particles(instances, alpha: float) -> None:
    alpha = max(0.0, min(1.0, float(alpha)))
    for item in instances:
        fields = ('x', 'y', 'rotation') if hasattr(item, 'rotation') else ('x', 'y')
        for field in fields:
            previous = float(getattr(item, f'_tick_prev_{field}', getattr(item, field, 0.0)))
            current = float(getattr(item, field, previous))
            setattr(item, f'_render_{field}', previous + (current - previous) * alpha)


def interpolate_effects(instances, alpha: float) -> None:
    alpha = max(0.0, min(1.0, float(alpha)))
    for item in instances:
        interpolate = getattr(item, 'apply_frame_interpolation', None)
        if callable(interpolate):
            try:
                interpolate(alpha)
                continue
            except Exception:
                pass
        for field, default in (('age', 0), ('x', 0), ('y', 0), ('opacity', 1), ('scale', 1), ('rotation', 0)):
            previous = float(getattr(item, f'_tick_prev_{field}', getattr(item, field, default)))
            current = float(getattr(item, field, previous))
            setattr(item, f'_render_{field}', previous + (current - previous) * alpha)


class FeedbackScene:
    """Own existing script instances without subscribing to desktop events.

    Coordinates are host-local. The caller drives ``advance`` only while its
    view is active and wraps ``batch`` in its own transform and clip.
    """

    def __init__(self, *, particle_manager=None, effect_manager=None,
                 resource_loader=load_effect_resource, tick_seconds: float = .05):
        if not math.isfinite(tick_seconds) or tick_seconds <= 0:
            raise ValueError('tick_seconds must be positive and finite')
        self._particle_manager = particle_manager
        self._effect_manager = effect_manager
        self._resource_loader = resource_loader
        self._tick_seconds = float(tick_seconds)
        self._accumulator = 0.0
        self._particles = []
        self._effects = []
        self._sequence = 0

    @property
    def particles(self) -> tuple:
        return tuple(self._particles)

    @property
    def effects(self) -> tuple:
        return tuple(self._effects)

    def _initialize(self, items, options, default_layer):
        for item in items:
            self._sequence += 1
            item.layer = normalize_layer(options.get('layer', getattr(item, 'layer', default_layer)), default_layer)
            item.z = int(options.get('z', getattr(item, 'z', 0)))
            item._draw_order = self._sequence
        return items

    def spawn_particle(self, particle_id, area_type, area_data, options=None) -> list:
        values = dict(options or {})
        items = create_particle_instances(self._particle_manager, particle_id, area_type, area_data, values)
        self._initialize(items, values, Layer.PARTICLE)
        prepare_particles(items)
        interpolate_particles(items, 1)
        self._particles.extend(items)
        return items

    def spawn_effect(self, effect_id, anchor_type, anchor_data, options=None, *, resource=None) -> list:
        values = dict(options or {})
        if resource is not None:
            values.setdefault('resource_path', resource.resource_id)
        elif values.get('resource_path'):
            resource = self._resource_loader(values['resource_path'], values)
            if resource is None:
                return []
        items = create_effect_instances(self._effect_manager, effect_id, anchor_type, anchor_data, values)
        items = [item for item in items if resource is not None or getattr(item, 'text', '')]
        self._initialize(items, values, Layer.EFFECT)
        for item in items:
            if resource is not None:
                item._visual_resource = resource
        prepare_effects(items)
        interpolate_effects(items, 1)
        self._effects.extend(items)
        return items

    def remove(self, instance) -> None:
        self._particles = [item for item in self._particles if item is not instance]
        self._effects = [item for item in self._effects if item is not instance]

    def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            return
        self._accumulator += seconds
        while self._accumulator + 1e-9 >= self._tick_seconds:
            self._accumulator = max(0.0, self._accumulator - self._tick_seconds)
            prepare_particles(self._particles)
            prepare_effects(self._effects)
            self._particles = update_instances(self._particles)
            self._effects = update_instances(self._effects)
        alpha = self._accumulator / self._tick_seconds
        interpolate_particles(self._particles, alpha)
        interpolate_effects(self._effects, alpha)

    def batch(self) -> DrawBatch:
        effects = build_effect_batch(self._effects)
        particles = build_particle_batch(self._particles)
        return DrawBatch(effects.commands + particles.commands,
                         effects.resource_revisions + particles.resource_revisions)

    def clear(self) -> None:
        self._particles.clear()
        self._effects.clear()
        self._accumulator = 0.0
        self._sequence = 0
