"""Backend-neutral visual presenters for transient particles and effects.

This module intentionally contains no toolkit or backend imports.  Qt and
DirectX consume the same immutable command descriptions generated here.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from config.config import PARTICLES, SPEAKER_AUDIO
from config.config_ui import COLORS, UI_THEME
from config.font_config import FONT, get_digit_font_family, get_ui_font_family
from config.scale import scale_px
from lib.core.graphics.commands import (
    DrawBatch,
    EllipseCommand,
    LineCommand,
    RectCommand,
    ResourceRevision,
    SpriteCommand,
    TextAlignment,
    TextCommand,
    TransformPop,
    TransformPush,
)
from lib.core.graphics.image_loader import load_image_resource, resize_image_resource
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.screen import clamp_rect_position
from lib.core.graphics.types import Color, FontSpec, Point, Rect, Size
from lib.core.layer import Layer, normalize_layer
from lib.core.world_objects import format_clock_countdown


def _alive(value: object) -> bool:
    alive = getattr(value, "alive", True)
    try:
        return bool(alive() if callable(alive) else alive)
    except Exception:
        return False


def _color(value: object, fallback: Color = Color()) -> Color:
    if isinstance(value, Color):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            return Color(*value[:4]) if len(value) >= 4 else Color(*value[:3])
        except (TypeError, ValueError):
            return fallback
    return fallback


def _particle_bloom_color(base: Color) -> Color:
    white_mix = 0.62
    return Color(
        int(base.red * (1.0 - white_mix) + 255 * white_mix),
        int(base.green * (1.0 - white_mix) + 255 * white_mix),
        int(base.blue * (1.0 - white_mix) + 255 * white_mix),
    )


def _particle_alpha(particle: object) -> float:
    # Qt's no_fade path deliberately uses opaque alpha, regardless of the
    # optional alpha_override field.  This is part of the visual contract.
    if bool(getattr(particle, "no_fade", False)):
        return 1.0
    life = max(0.0, float(getattr(particle, "life", 0.0)))
    max_life = max(1e-6, float(getattr(particle, "max_life", 1.0)))
    threshold = max(1e-6, float(PARTICLES.get("fade_threshold", 0.75) or 0.75))
    fade_start = max_life * threshold
    alpha = 1.0 if life >= fade_start else life / fade_start
    override = getattr(particle, "alpha_override", None)
    if override is not None:
        try:
            alpha = min(alpha, max(0.0, min(1.0, float(override) / 255.0)))
        except (TypeError, ValueError):
            pass
    return max(0.0, min(1.0, alpha))


def _position(value: object) -> tuple[float, float]:
    return (
        float(getattr(value, "_render_x", getattr(value, "x", 0.0))),
        float(getattr(value, "_render_y", getattr(value, "y", 0.0))),
    )


def _particle_font(particle: object) -> FontSpec:
    value = getattr(particle, "font", None)
    if isinstance(value, FontSpec):
        return value
    family = get_ui_font_family()
    try:
        pixel_size = max(1, int(getattr(particle, "font_size", 12) or 12))
    except (TypeError, ValueError):
        pixel_size = 12
    return FontSpec(family, pixel_size)


def _ordered(items: list[object], default_layer: Layer) -> list[object]:
    return sorted(
        (item for item in items if _alive(item)),
        key=lambda item: (
            int(getattr(item, "layer", default_layer)),
            int(getattr(item, "z", 0)),
            int(getattr(item, "_draw_order", 0)),
        ),
    )


def sample_motor_jitter(is_moving: bool, *, rng: random.Random | None = None) -> Point:
    """Sample the Qt-reference integer jitter used by motor sprites."""
    source = rng or random
    base_amplitude = 3 if bool(is_moving) else 1
    scaled = base_amplitude * 0.5
    low = int(scaled)
    high = low if scaled == low else low + 1
    amplitude = low
    if high != low and source.random() < scaled - low:
        amplitude = high
    return Point(
        source.randint(-amplitude, amplitude),
        source.randint(-amplitude, amplitude),
    )


def update_speaker_intensity(current: float, sample: float | None) -> float:
    """Apply the shared asymmetric EMA used by the speaker visual."""
    current = max(0.0, min(1.0, float(current)))
    sample = 0.0 if sample is None else max(0.0, min(1.0, float(sample)))
    attack = float(SPEAKER_AUDIO.get("ema_attack", 0.35))
    decay = float(SPEAKER_AUDIO.get("ema_decay", 0.08))
    alpha = attack if sample > current else decay
    return alpha * sample + (1.0 - alpha) * current


def resolve_speaker_scale(intensity: float) -> tuple[float, float]:
    """Resolve frequency intensity to the shared center-anchored scale."""
    value = max(0.0, min(1.0, float(intensity)))
    exponent = float(SPEAKER_AUDIO.get("scale_exp", 2.0))
    scale_range = float(SPEAKER_AUDIO.get("scale_range", 0.1))
    response_gain = max(0.0, float(SPEAKER_AUDIO.get("response_gain", 4.0)))
    amplitude = min(1.0, (value ** exponent) * response_gain)
    return 1.0 + amplitude * scale_range, 1.0 - amplitude * scale_range


def build_particle_batch(particles: list[object]) -> DrawBatch:
    """Resolve particle state into one immutable declaration batch."""
    commands = []
    enable_stroke = bool(PARTICLES.get("enable_stroke", True))
    border = _color(UI_THEME.get("border"), Color(0, 0, 0))
    for particle in _ordered(particles, Layer.PARTICLE):
        alpha = _particle_alpha(particle)
        if alpha <= 0.0:
            continue
        layer = normalize_layer(getattr(particle, "layer", Layer.PARTICLE), Layer.PARTICLE)
        z = int(getattr(particle, "z", 0))
        order = int(getattr(particle, "_draw_order", 0))
        color = _color(getattr(particle, "color", None)).with_alpha(255)
        x, y = _position(particle)

        if bool(getattr(particle, "is_text", False)):
            text = str(getattr(particle, "text", ""))
            font = _particle_font(particle)
            measured_width = getattr(particle, "_text_w", None)
            try:
                width = float(measured_width) if measured_width is not None else len(text) * font.pixel_size * 0.72
            except (TypeError, ValueError):
                width = len(text) * font.pixel_size * 0.72
            width = max(font.pixel_size * 2.0, width)
            measured_height = getattr(particle, "_text_h", None)
            try:
                height = float(measured_height) if measured_height is not None else max(font.pixel_size * 1.6, 12.0)
            except (TypeError, ValueError):
                height = max(font.pixel_size * 1.6, 12.0)
            rect = Rect(x - width / 2.0, y - height / 2.0, width, height)
            bloom = max(0.0, float(getattr(particle, "bloom", 0.0) or 0.0))
            text_alpha = alpha
            override = getattr(particle, "alpha_override", None)
            if override is not None:
                try:
                    text_alpha = max(0.0, min(1.0, float(override) / 255.0))
                except (TypeError, ValueError):
                    pass
            if bloom > 0.0:
                glow = _particle_bloom_color(_color(getattr(particle, "color", None)))
                # Match Qt's eight-direction bloom and 30% alpha budget.
                for distance_scale, alpha_scale in ((0.45, 0.10), (0.75, 0.07), (1.0, 0.04)):
                    distance = bloom * distance_scale
                    for dx, dy in (
                        (distance, 0.0), (-distance, 0.0), (0.0, distance), (0.0, -distance),
                        (distance * 0.7, distance * 0.7), (-distance * 0.7, distance * 0.7),
                        (distance * 0.7, -distance * 0.7), (-distance * 0.7, -distance * 0.7),
                    ):
                        commands.append(TextCommand(
                            text, font, glow, Rect(rect.x + dx, rect.y + dy, rect.width, rect.height),
                            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                            alpha=text_alpha * 0.30 * alpha_scale, layer=layer, z=z, order=order,
                        ))
            commands.append(TextCommand(
                text, font, color, rect,
                alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                alpha=text_alpha, layer=layer, z=z, order=order,
            ))
            continue

        if bool(getattr(particle, "is_line", False)):
            length = float(getattr(particle, "length", 0.0))
            if length > 0.5:
                end = Point(
                    x + float(getattr(particle, "line_dx", 0.0)) * length,
                    y + float(getattr(particle, "line_dy", 0.0)) * length,
                )
                width = max(0.0, float(getattr(particle, "pen_width", 1.0)))
                commands.append(LineCommand(
                    Point(x, y), end,
                    color, width=width,
                    alpha=alpha, layer=layer, z=z, order=order,
                ))
                if width > 0.0:
                    radius = width / 2.0
                    for point in (Point(x, y), end):
                        commands.append(EllipseCommand(
                            Rect(point.x - radius, point.y - radius, width, width),
                            fill=color, alpha=alpha, layer=layer, z=z, order=order,
                        ))
            continue

        if hasattr(particle, "width") and hasattr(particle, "height"):
            width = max(0.0, float(getattr(particle, "width", 0.0)))
            height = max(0.0, float(getattr(particle, "height", 0.0)))
            commands.append(RectCommand(
                Rect(x, y - height / 2.0, width, height), fill=color,
                stroke=border.with_alpha(255) if enable_stroke else None,
                alpha=alpha, layer=layer, z=z, order=order,
            ))
            continue

        size = max(0.0, float(getattr(particle, "size", 0.0)))
        radius = size if bool(getattr(particle, "is_circle", False)) else size / 2.0
        command_type = EllipseCommand if bool(getattr(particle, "is_circle", False)) else RectCommand
        commands.append(command_type(
            Rect(x - radius, y - radius, radius * 2.0, radius * 2.0), fill=color,
            stroke=border.with_alpha(255) if enable_stroke else None,
            alpha=alpha, layer=layer, z=z, order=order,
        ))
    return DrawBatch(tuple(commands))


def build_command_shell_batch(
    width: float,
    height: float,
    *,
    layer: int = int(Layer.PET_UI),
    border_layer: float = 2.0,
) -> DrawBatch:
    """Build the black/cyan/pink shell used by the Qt command dialog."""
    width = max(0.0, float(width))
    height = max(0.0, float(height))
    border_layer = max(0.0, float(border_layer))
    content_inset = border_layer * 2.0
    return DrawBatch((
        RectCommand(
            Rect(0.0, 0.0, width, height),
            fill=_color(COLORS.get("black")),
            layer=layer,
        ),
        RectCommand(
            Rect(
                border_layer,
                border_layer,
                max(0.0, width - border_layer * 2.0),
                max(0.0, height - border_layer * 2.0),
            ),
            fill=_color(COLORS.get("cyan")),
            layer=layer,
            z=1,
        ),
        RectCommand(
            Rect(
                content_inset,
                content_inset,
                max(0.0, width - content_inset * 2.0),
                max(0.0, height - content_inset * 2.0),
            ),
            fill=_color(COLORS.get("pink")),
            layer=layer,
            z=2,
        ),
    ))


def build_command_panel_batch(
    width: float,
    height: float,
    text: str,
    composition: str = "",
    *,
    layer: int = int(Layer.PET_UI),
    border_layer: float = 2.0,
) -> DrawBatch:
    """Build the shared Qt-reference shell and editable command field."""
    shell = build_command_shell_batch(
        width,
        height,
        layer=layer,
        border_layer=border_layer,
    )
    value = str(text or "") + str(composition or "")
    placeholder = not value
    if placeholder:
        value = "cmd"
    field_inset = border_layer * 2.0
    field_rect = Rect(
        field_inset,
        field_inset,
        max(0.0, float(width) - field_inset * 2.0),
        max(0.0, float(height) - field_inset * 2.0),
    )
    font = FontSpec(get_ui_font_family(), max(1, int(FONT.get("cmd_size", 12))))
    commands = list(shell.commands)
    commands.extend((
        RectCommand(
            field_rect,
            fill=Color(255, 255, 255),
            stroke=_color(COLORS.get("pink")),
            stroke_width=border_layer,
            layer=layer,
            z=3,
        ),
        TextCommand(
            value,
            font,
            Color(127, 127, 127) if placeholder else _color(COLORS.get("black")),
            Rect(
                field_rect.x + border_layer + 4.0,
                field_rect.y + border_layer,
                max(0.0, field_rect.width - (border_layer + 4.0) * 2.0),
                max(0.0, field_rect.height - border_layer * 2.0),
            ),
            alignment=int(TextAlignment.LEFT | TextAlignment.VCENTER),
            layer=layer,
            z=4,
        ),
    ))
    return DrawBatch(tuple(commands))


def resolve_command_panel_geometry(
    pet_rect: Rect,
    panel_size: tuple[float, float],
    screen_rect: Rect,
    *,
    offset_x: float = 6.0,
    offset_y: float = 0.0,
) -> Rect:
    """Resolve the Qt-reference command panel anchor in desktop pixels."""
    width = max(1, int(round(float(panel_size[0]))))
    height = max(1, int(round(float(panel_size[1]))))
    center_y = pet_rect.y + pet_rect.height / 2.0
    candidate_y = int(round(center_y - height / 2.0 + float(offset_y)))
    right_x = int(round(pet_rect.x + pet_rect.width + float(offset_x)))
    left_x = int(round(pet_rect.x - width - float(offset_x)))
    screen_right = screen_rect.x + screen_rect.width
    candidate_x = right_x if right_x + width <= screen_right else left_x
    x, y, _ = clamp_rect_position(
        candidate_x,
        candidate_y,
        width,
        height,
        screen_rect,
    )
    return Rect(x, y, width, height)


def build_world_object_batch(
    resource: ImageResource,
    frame_index: int,
    *,
    alpha: float = 1.0,
    flipped: bool = False,
    order: int = 0,
    object_type: str | None = None,
    countdown_centis: int | None = None,
    position: Point | None = None,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> DrawBatch:
    """Resolve one world-object sprite without backend-owned draw logic."""
    if not isinstance(resource, ImageResource):
        raise TypeError("world object visual requires an ImageResource")
    index = int(frame_index) % len(resource.frames)
    frame = resource.frames[index]
    draw_position = position if isinstance(position, Point) else Point()
    scale_x = max(0.001, float(scale_x))
    scale_y = max(0.001, float(scale_y))
    transformed = abs(scale_x - 1.0) > 1e-6 or abs(scale_y - 1.0) > 1e-6
    commands = []
    if transformed:
        center_x = resource.size[0] / 2.0
        center_y = resource.size[1] / 2.0
        commands.append(TransformPush((
            scale_x,
            0.0,
            0.0,
            scale_y,
            center_x * (1.0 - scale_x),
            center_y * (1.0 - scale_y),
        )))
    commands.append(SpriteCommand(
            resource.resource_id,
            1,
            index,
            frame,
            position=draw_position,
            alpha=alpha,
            flipped=flipped,
            scale=1.0,
            layer=int(Layer.WORLD_OBJECT),
            z=0,
            order=order,
            target_size=Size(*resource.size),
        ))
    if transformed:
        commands.append(TransformPop())
    if str(object_type or "").strip().lower() == "clock" and countdown_centis is not None:
        width, height = resource.size
        font = FontSpec(get_digit_font_family(), max(10, int(min(width, height) * 0.14)), True)
        commands.append(TextCommand(
            format_clock_countdown(countdown_centis),
            font,
            _color(UI_THEME.get("deep_blue"), Color(35, 76, 128)),
            Rect(0.0, -scale_px(10), float(width), float(height)),
            alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            alpha=alpha,
            layer=int(Layer.WORLD_OBJECT),
            z=1,
            order=order,
        ))
    return DrawBatch(tuple(commands), (ResourceRevision(resource.resource_id, 1),))


def _effect_font(effect: object) -> FontSpec:
    font_type = str(getattr(effect, "font_type", "ui") or "ui").lower()
    family = get_digit_font_family() if font_type in {"digit", "number", "lahai"} else get_ui_font_family()
    weight = getattr(effect, "font_weight", None)
    bold = bool(getattr(effect, "font_bold", False))
    try:
        bold = bold or int(weight) >= 63
    except (TypeError, ValueError):
        pass
    return FontSpec(family, max(1, int(getattr(effect, "font_size", 32))), bold)


def load_effect_resource(path: str | Path, options: dict | None = None) -> ImageResource | None:
    """Load, size and feather an effect image using shared visual semantics."""
    resource = load_image_resource(Path(path))
    if resource is None:
        return None
    values = dict(options or {})
    edge_feather = bool(values.get("edge_feather", False))
    output_size = values.get("masked_output_size")
    if edge_feather and isinstance(output_size, (list, tuple)) and len(output_size) >= 2:
        try:
            resource = resize_image_resource(
                resource,
                (max(1, int(output_size[0])), max(1, int(output_size[1]))),
                keep_aspect=True,
            )
        except (TypeError, ValueError):
            pass
    if not edge_feather:
        return resource
    try:
        ratio = max(0.0, min(0.45, float(values.get("feather_ratio", 0.12) or 0.12)))
    except (TypeError, ValueError):
        ratio = 0.12
    if ratio <= 0.0:
        return resource

    feathered_frames = []
    for frame in resource.frames:
        pixels = bytearray(frame.pixels)
        feather_x = max(1.0, frame.width * ratio)
        feather_y = max(1.0, frame.height * ratio)
        for y in range(frame.height):
            vertical = min(1.0, (y + 0.5) / feather_y, (frame.height - y - 0.5) / feather_y)
            for x in range(frame.width):
                horizontal = min(1.0, (x + 0.5) / feather_x, (frame.width - x - 0.5) / feather_x)
                factor = max(0.0, min(1.0, horizontal * vertical))
                alpha_index = (y * frame.width + x) * 4 + 3
                pixels[alpha_index] = round(pixels[alpha_index] * factor)
        feathered_frames.append(RasterFrame(frame.width, frame.height, bytes(pixels), frame.duration_ms))
    return ImageResource(f"{resource.resource_id}@feather:{ratio:.4f}", tuple(feathered_frames))


def build_effect_batch(effects: list[object]) -> DrawBatch:
    """Resolve image/text effects into one immutable declaration batch."""
    commands = []
    revisions: list[ResourceRevision] = []
    seen_resources: set[str] = set()
    for effect in _ordered(effects, Layer.EFFECT):
        opacity = max(0.0, min(1.0, float(
            getattr(effect, "_render_opacity", getattr(effect, "opacity", 1.0))
        )))
        if opacity <= 0.0:
            continue
        layer = int(getattr(effect, "layer", Layer.EFFECT))
        z = int(getattr(effect, "z", 0))
        order = int(getattr(effect, "_draw_order", 0))
        resource = getattr(effect, "_visual_resource", getattr(effect, "_dx_resource", None))
        if isinstance(resource, ImageResource):
            frame = resource.frames[0]
            scale = max(0.001, float(
                getattr(effect, "_render_scale", getattr(effect, "scale", 1.0))
            ))
            x, y = _position(effect)
            half_w = frame.width * scale / 2.0
            half_h = frame.height * scale / 2.0
            rotation = math.radians(float(
                getattr(effect, "_render_rotation", getattr(effect, "rotation", 0.0))
            ))
            if rotation:
                commands.append(TransformPush((
                    math.cos(rotation),
                    math.sin(rotation),
                    -math.sin(rotation),
                    math.cos(rotation),
                    x,
                    y,
                )))
                position = Point(-half_w, -half_h)
            else:
                position = Point(x - half_w, y - half_h)
            commands.append(SpriteCommand(
                resource.resource_id,
                1,
                0,
                frame,
                position,
                opacity,
                False,
                scale,
                layer,
                z,
                order,
            ))
            if rotation:
                commands.append(TransformPop())
            if resource.resource_id not in seen_resources:
                seen_resources.add(resource.resource_id)
                revisions.append(ResourceRevision(resource.resource_id, 1))
            continue
        text = str(getattr(effect, "text", "") or "")
        if not text:
            continue
        size = max(1, int(getattr(effect, "font_size", 32)))
        width = max(size * 2.0, len(text) * size * 0.72)
        height = max(size * 1.6, 12.0)
        x, y = _position(effect)
        rect = Rect(x - width / 2.0, y - height / 2.0, width, height)
        font = _effect_font(effect)
        color = _color(getattr(effect, "color", None))
        glow = max(0.0, float(getattr(effect, "glow", 0.0) or 0.0))
        glow_color = _color(getattr(effect, "glow_color", None), color)
        if glow > 0.0:
            for distance_scale, alpha_scale in ((0.45, 0.10), (0.75, 0.07), (1.0, 0.04)):
                distance = glow * distance_scale
                for dx, dy in (
                    (distance, 0.0),
                    (-distance, 0.0),
                    (0.0, distance),
                    (0.0, -distance),
                    (distance * 0.7, distance * 0.7),
                    (-distance * 0.7, distance * 0.7),
                    (distance * 0.7, -distance * 0.7),
                    (-distance * 0.7, -distance * 0.7),
                ):
                    commands.append(TextCommand(
                        text,
                        font,
                        glow_color,
                        Rect(rect.x + dx, rect.y + dy, rect.width, rect.height),
                        int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                        opacity * 0.30 * alpha_scale,
                        layer,
                        z,
                        order,
                    ))
        commands.append(TextCommand(
            text,
            font,
            color,
            rect,
            int(TextAlignment.HCENTER | TextAlignment.VCENTER),
            opacity,
            layer,
            z,
            order,
        ))
    return DrawBatch(tuple(commands), tuple(revisions))


__all__ = [
    "build_command_panel_batch",
    "build_command_shell_batch",
    "build_effect_batch",
    "build_particle_batch",
    "build_world_object_batch",
    "load_effect_resource",
    "resolve_speaker_scale",
    "resolve_command_panel_geometry",
    "sample_motor_jitter",
    "update_speaker_intensity",
]
