"""Backend-neutral text elision and measurement helpers.

Widths come from an injected low-level metrics adapter (the same pattern the
bubble/command-hint presenters already use); what belongs to the shared layer is
*which* text survives, not how wide a glyph is.
"""
from __future__ import annotations

from typing import Protocol


class TextWidthMetrics(Protocol):
    """Minimal low-level width query supplied by a rendering adapter."""

    def measure(self, text: str) -> float:
        ...


#: Marker appended when text does not fit, matching the Qt baseline.
ELLIPSIS = "..."


def elide_right(text: str, max_width: float, metrics: TextWidthMetrics) -> str:
    """Return ``text`` truncated from the right so it fits ``max_width``.

    Mirrors the baseline "keep the head, drop the tail" behaviour used by the
    tray menu and media panels. Degenerate inputs are handled explicitly so no
    backend has to special-case them.
    """
    resolved = str(text or "")
    try:
        limit = float(max_width)
    except (TypeError, ValueError):
        return resolved
    if limit <= 0.0:
        return ""
    if metrics.measure(resolved) <= limit:
        return resolved

    marker_width = metrics.measure(ELLIPSIS)
    if marker_width > limit:
        return ""

    low, high = 0, len(resolved)
    while low < high:
        middle = (low + high + 1) // 2
        if metrics.measure(resolved[:middle]) + marker_width <= limit:
            low = middle
        else:
            high = middle - 1
    return resolved[:low] + ELLIPSIS


__all__ = ["ELLIPSIS", "TextWidthMetrics", "elide_right"]
