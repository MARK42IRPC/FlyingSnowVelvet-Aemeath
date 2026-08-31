from __future__ import annotations

import unittest

from lib.core.dx_bridge.text_metrics import (
    DirectWriteTextMetrics,
    create_directwrite_text_metrics,
)
from lib.core.graphics.rich_text_parser import TextSegment
from lib.core.graphics.types import FontSpec


class _Target:
    def __init__(self) -> None:
        self.calls: list[tuple[str, FontSpec]] = []

    def measure_text(self, text: str, font: FontSpec) -> tuple[float, float]:
        self.calls.append((text, font))
        return len(text) * font.pixel_size * 0.5, font.pixel_size + 3.0


def _metrics(target: _Target) -> DirectWriteTextMetrics:
    return DirectWriteTextMetrics(
        target,
        FontSpec("UI", 12, bold=True),
        FontSpec("Digits", 14),
        FontSpec("Side", 10),
        15.0,
        17.0,
        12.0,
        3.0,
        13.0,
        4.0,
    )


class DirectWriteTextMetricsTests(unittest.TestCase):
    def test_measure_selects_default_digit_and_side_fonts(self):
        target = _Target()
        metrics = _metrics(target)

        metrics.measure("正文")
        metrics.measure("123", digit=True)
        metrics.measure("Aemeath", side=True)

        self.assertEqual(
            [font.family for _text, font in target.calls],
            ["UI", "Digits", "Side"],
        )

    def test_measure_caches_identical_text_and_font(self):
        target = _Target()
        metrics = _metrics(target)

        first = metrics.measure("cached")
        second = metrics.measure("cached")

        self.assertEqual(first, second)
        self.assertEqual(len(target.calls), 1)

    def test_measure_segment_applies_scale(self):
        target = _Target()
        metrics = _metrics(target)

        width = metrics.measure_segment(TextSegment("scale", scale=1.5))

        self.assertEqual(width, 45.0)

    def test_factory_returns_none_without_native_measurement(self):
        self.assertIsNone(create_directwrite_text_metrics(object()))

    def test_factory_uses_native_heights_and_portable_font_contract(self):
        target = _Target()

        metrics = create_directwrite_text_metrics(target)

        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertGreaterEqual(metrics.default_line_height, 15.0)
        self.assertGreaterEqual(metrics.digit_line_height, 15.0)
        self.assertEqual(target.calls[0][0], "Hg国")
        self.assertEqual(target.calls[1][0], "Hg09")


if __name__ == "__main__":
    unittest.main()
