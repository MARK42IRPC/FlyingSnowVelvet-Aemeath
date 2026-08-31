"""DirectWrite-backed low-level metrics for shared visual presenters."""
from __future__ import annotations

from dataclasses import dataclass, field

from lib.core.graphics.application_visuals import (
    create_portable_bubble_text_metrics,
    create_portable_command_hint_metrics,
)
from lib.core.graphics.rich_text_parser import TextSegment
from lib.core.graphics.types import FontSpec


@dataclass(slots=True)
class DirectWriteTextMetrics:
    _target: object
    default_font: FontSpec
    digit_font: FontSpec
    side_font: FontSpec
    default_line_height: float
    digit_line_height: float
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float
    _cache: dict[tuple[str, int, bool, str], float] = field(default_factory=dict)

    def measure(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        font = self.side_font if side else (self.digit_font if digit else self.default_font)
        value = str(text or "")
        key = (font.family, int(font.pixel_size), bool(font.bold), value)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        width, _height = self._target.measure_text(value, font)
        width = max(0.0, float(width))
        if len(self._cache) >= 2048:
            self._cache.clear()
        self._cache[key] = width
        return width

    def measure_segment(self, segment: TextSegment) -> float:
        return self.measure(segment.text) * segment.scale


def create_directwrite_text_metrics(target: object) -> DirectWriteTextMetrics | None:
    measure_text = getattr(target, "measure_text", None)
    if not callable(measure_text):
        return None
    command = create_portable_command_hint_metrics()
    bubble = create_portable_bubble_text_metrics()
    try:
        _default_width, default_height = measure_text("Hg国", command.default_font)
        _digit_width, digit_height = measure_text("Hg09", command.digit_font)
    except Exception:
        return None
    default_height = max(1.0, float(default_height))
    digit_height = max(1.0, float(digit_height))
    return DirectWriteTextMetrics(
        target,
        command.default_font,
        command.digit_font,
        command.side_font,
        max(default_height, bubble.default_line_height),
        max(digit_height, bubble.digit_line_height),
        default_height * 0.8,
        default_height * 0.2,
        digit_height * 0.8,
        digit_height * 0.2,
    )


__all__ = ["DirectWriteTextMetrics", "create_directwrite_text_metrics"]
