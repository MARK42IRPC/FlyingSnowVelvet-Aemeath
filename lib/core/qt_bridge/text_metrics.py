"""Qt adapter that exposes only low-level glyph metrics to shared presenters.

Presenters own wrapping, elision, baseline placement and alignment; the Qt host
may only report measured advances and vertical metrics for the fonts it will
actually rasterize. Several widgets used to carry their own copy of this
adapter, which is consolidated here.
"""

from __future__ import annotations

from PyQt5.QtGui import QFont, QFontMetrics, QTextLayout

from lib.core.graphics.rich_text_parser import TextSegment
from lib.core.graphics.types import FontSpec


def _font_spec(font: QFont) -> FontSpec:
    return FontSpec(font.family(), font.pixelSize(), font.bold())


class QtTextMetrics:
    """Expose Qt's low-level font metrics as backend-neutral measurements."""

    def __init__(self, default_font: QFont, digit_font: QFont | None = None, *, side_font: QFont | None = None) -> None:
        self._default_font_qt = default_font
        self._digit_font_qt = digit_font if digit_font is not None else default_font
        self._side_font_qt = side_font
        self._default_metrics = QFontMetrics(default_font)
        self._digit_metrics = QFontMetrics(digit_font if digit_font is not None else default_font)
        self._side_metrics = None if side_font is None else QFontMetrics(side_font)
        self.default_font = _font_spec(default_font)
        self.digit_font = _font_spec(digit_font if digit_font is not None else default_font)
        self.side_font = _font_spec(side_font if side_font is not None else default_font)
        self.default_line_height = float(self._default_metrics.height())
        self.digit_line_height = float(self._digit_metrics.height())
        self.default_ascent = float(self._default_metrics.ascent())
        self.default_descent = float(self._default_metrics.descent())
        self.digit_ascent = float(self._digit_metrics.ascent())
        self.digit_descent = float(self._digit_metrics.descent())

    def measure(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        if side and self._side_metrics is not None:
            metrics = self._side_metrics
        elif digit:
            metrics = self._digit_metrics
        else:
            metrics = self._default_metrics
        return float(metrics.horizontalAdvance(str(text or "")))

    def measure_segment(self, segment: TextSegment) -> float:
        """Measure a rich text segment with the style and scale it will draw at."""
        font = QFont(self._default_font_qt)
        font.setPixelSize(max(1, int(round(font.pixelSize() * segment.scale))))
        if segment.style in {"bold", "bold_italic"}:
            font.setBold(True)
        elif segment.style == "code":
            font.setFamily("Consolas")
        return float(QFontMetrics(font).horizontalAdvance(segment.text))

    def ascent_for(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        """Return the line ascent Qt will use when it draws ``text``.

        Qt lays the run out itself, so a glyph missing from the primary font is
        served by a fallback font whose ascent can differ. Presenters need that
        number to place a shared baseline exactly, so the adapter reports it
        through the same text layout Qt will rasterize.
        """
        value = str(text or "")
        if side and self._side_font_qt is not None:
            font = self._side_font_qt
        elif digit:
            font = self._digit_font_qt
        else:
            font = self._default_font_qt
        if not value:
            return float(QFontMetrics(font).ascent())
        layout = QTextLayout(value, font)
        layout.beginLayout()
        try:
            line = layout.createLine()
            if not line.isValid():
                return float(QFontMetrics(font).ascent())
            line.setLineWidth(1e9)
            return float(line.ascent())
        finally:
            layout.endLayout()


__all__ = ["QtTextMetrics"]
