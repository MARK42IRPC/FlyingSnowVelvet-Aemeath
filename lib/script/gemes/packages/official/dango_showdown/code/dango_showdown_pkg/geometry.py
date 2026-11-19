"""Small geometry helpers shared by combat and presentation (pure Python)."""

from __future__ import annotations

import math

from .model import Dango


def beam_through(owner: Dango, target: Dango, angle: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Make a visual beam through the target centre at ``angle``.

    The caster -> target segment is replaced by the parallel line through the
    target, so the target centre is exactly on the drawn beam. The caller draws
    the beam from the caster to the target and the matching reflection, which
    makes both halves of the beam share one random direction.
    """
    return beam_through_points(float(owner.x), float(owner.y), float(target.x), float(target.y), angle)


def beam_through_points(ox: float, oy: float, tx: float, ty: float, angle: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """``beam_through`` over plain coordinates (used for event snapshots)."""
    cos_a = math.cos(float(angle))
    sin_a = math.sin(float(angle))
    source_x = float(ox) - float(tx)
    source_y = float(oy) - float(ty)
    projection = cos_a * source_x + sin_a * source_y
    return (tx + cos_a * projection, ty + sin_a * projection), (tx, ty)


__all__ = ["beam_through", "beam_through_points"]
