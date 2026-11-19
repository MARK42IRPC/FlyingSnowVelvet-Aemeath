"""Stretch/deform parameters for idle, attack and hurt (pure Python)."""

from __future__ import annotations

import math

from .constants import (
    ATTACK_STRETCH_AMPLITUDE,
    ATTACK_STRETCH_FREQ,
    HURT_FLASH_PERIOD,
    HURT_STRETCH_FREQ,
    HURT_FLATTEN,
    IDLE,
    IDLE_STRETCH_AMPLITUDE,
    IDLE_STRETCH_FREQ,
)
from .model import Dango


def selection_breath(seconds: float, phase_offset: float = 0.0) -> tuple[float, float, float]:
    """Continuous portrait breathing: area-preserving scale and a tiny lift.

    Selection uses the presentation clock, independent of the combat state.
    Keep these fractional values in an affine transform; rounding a 3 px
    movement into sprite positions would leave the portrait still for seconds.
    """
    phase = float(seconds) * 1.7 + float(phase_offset)
    wave = math.sin(phase)
    scale_x = 1.0 + wave * .014
    return scale_x, 1.0 / scale_x, wave * 1.5


def _envelope(progress: float) -> float:
    return max(0.0, 1.0 - max(0.0, min(1.0, progress)))


def deform(state: str, state_seconds: float, phase: float, duration: float) -> tuple[float, float]:
    """Return ``(scale_x, scale_y)`` with screen-x/y semantics (width, height).

    Idle/attack stay area preserving. The hurt pose keeps its flattened ratio at
    every instant instead of multiplying a rotating wobble into the squash:
    a non-uniform hurt scale is then exactly ``width * 0.72`` by
    ``height * 0.72``, so the body visibly spreads out rather than randomly
    turning tall on some frames.
    """
    if state == IDLE:
        wobble = math.sin(phase * 2.0 * math.pi * IDLE_STRETCH_FREQ) * IDLE_STRETCH_AMPLITUDE
        return 1.0 + wobble, 1.0 / (1.0 + wobble)
    progress = state_seconds / duration if duration > 0 else 1.0
    envelope = _envelope(progress)
    if state == "attack":
        wobble = math.sin(state_seconds * 2.0 * math.pi * ATTACK_STRETCH_FREQ) * ATTACK_STRETCH_AMPLITUDE * envelope
        return 1.0 + wobble, 1.0 / (1.0 + wobble)
    # Pinch in early, then hold the flattened pose so a 300 ms hit stays
    # legible; area (sx * sy) drops below 1 for the whole hurt pose.
    pinch = HURT_FLATTEN + (1.0 - HURT_FLATTEN) * envelope ** 2
    spread = (1.0 / HURT_FLATTEN - 1.0) * envelope
    wobble = math.sin(state_seconds * 2.0 * math.pi * HURT_STRETCH_FREQ) * 0.015 * envelope
    return (1.0 + spread) * pinch, pinch


def hit_flash_alpha(dango: Dango) -> float:  # noqa: D401
    """Alternating red tint while the hurt state is active."""
    if dango.state != "hurt" or dango.hurt_flash_seconds <= 0.0:
        return 0.0
    return 0.0 if int(dango.hurt_flash_seconds / HURT_FLASH_PERIOD) % 2 else 1.0


def mirrored(scale_x: float, scale_y: float, direction_x: float, direction_y: float) -> tuple[float, float, float, float]:
    """Rotate the stretch axis toward the impact direction.

    Returns ``(m11, m12, m21, m22)``: a rotation into the impact frame applied
    to a non-uniform scale, then rotated back (``R * S * R^T``).
    """
    length = math.hypot(direction_x, direction_y)
    if length <= 1e-6:
        return scale_x, 0.0, 0.0, scale_y
    cos_t = direction_x / length
    sin_t = direction_y / length
    a = cos_t * cos_t * scale_x + sin_t * sin_t * scale_y
    b = cos_t * sin_t * (scale_x - scale_y)
    return a, b, b, cos_t * cos_t * scale_y + sin_t * sin_t * scale_x
