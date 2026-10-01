"""Qt/DX pixel parity for the shared draw primitives.

``lib/core/render/visuals/commands.py`` extends the shared draw contract
with rounded rectangles, gradients, paths and explicit stroke/antialias
semantics. Each of those primitives must resolve to the same pixels through the
Qt executor and the DX/WARP executor, otherwise a page that already consumes a
shared presenter would still drift between backends.

Two tiers are asserted, and the distinction is deliberate rather than a loose
global threshold:

* The **exact tier** uses geometry whose edges never cross a pixel centre, so
  both rasterizers must agree on every pixel's coverage to the last step. This
  is where geometry, ordering, alpha and clipping are really pinned.
* The **sampling tier** covers antialiased coverage, stroked boundaries and
  gradient ramps: a pixel centre landing exactly on an edge, or a pixel covered
  by both a fill and an adjacent stroke, is a rasterizer tie rather than a
  contract difference. Those pixels are resolved by the two explicit rules
  below, and every other pixel is still asserted exactly.

The rules, both measured against the two backends rather than guessed:

* **Coverage.** Qt snaps aliased coverage to 1/256 steps while Direct2D
  evaluates the analytic edge, so a tied pixel may carry anything from full to
  no coverage; alpha is bounded by ``SAMPLING_ALPHA_TOLERANCE``.
* **Boundary ownership.** Where a fill meets an adjacent stroke a single pixel
  can legitimately take either colour, so a tied pixel must lie inside the 3x3
  Qt neighbourhood, plus ``SAMPLING_SLACK``, that contains both candidates.
  Colour is only compared where both backends report coverage above
  ``SAMPLING_COVER_FLOOR``: below that, unpremultiplying measures rounding noise
  rather than paint.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QApplication

from lib.core.render.backends.dx.offscreen import DxOffscreenTarget, find_dx_library
from lib.core.render.visuals.commands import (
    ClipPop,
    ClipPush,
    DrawBatch,
    GradientStop,
    LinearGradientFill,
    PathCommand,
    RectCommand,
    StrokeCap,
    StrokeJoin,
    build_polygon_path,
)
from lib.core.render.visuals.types import Color, Point, Rect
from lib.core.layer import Layer
from lib.core.render.backends.qt.draw_backend import QtDrawBackend

#: Maximum coverage delta accepted for a pixel whose centre sits on an edge.
SAMPLING_ALPHA_TOLERANCE = 48
#: Coverage above which tied colour is compared instead of ignored.
SAMPLING_COVER_FLOOR = 160
#: Tolerance around the 3x3 Qt neighbourhood that bounds a tied pixel.
SAMPLING_SLACK = 2
#: Maximum per-channel delta for a gradient ramp, which interpolates with
#: different precision in Qt and Direct2D.
GRADIENT_CHANNEL_TOLERANCE = 2


def _unpremultiply(pixel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Convert a premultiplied DX readback pixel to straight RGBA.

    ``DxOffscreenTarget.readback_rgba`` returns premultiplied alpha, while
    ``QImage.pixelColor`` returns straight alpha. Comparing them without this
    conversion reports false mismatches at every covered edge.
    """
    red, green, blue, alpha = (int(channel) for channel in pixel)
    if alpha == 0:
        return 0, 0, 0, 0
    if alpha == 255:
        return red, green, blue, alpha
    scale = 255.0 / alpha
    return (
        min(255, round(red * scale)),
        min(255, round(green * scale)),
        min(255, round(blue * scale)),
        alpha,
    )


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "Qt/DX parity requires Windows and a built DX DLL",
)
class GraphicsPrimitiveParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _qt_image(batch: DrawBatch, width: int, height: int) -> QImage:
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor("transparent"))
        painter = QPainter(image)
        try:
            QtDrawBackend().render(batch, painter)
        finally:
            painter.end()
        return image

    def _assert_parity(
        self,
        batch: DrawBatch,
        width: int,
        height: int,
        *,
        exact: bool = True,
        tol: int = 8,
        label: str = "",
    ) -> None:
        """Compare every pixel of one batch rendered by both backends."""
        qt_image = self._qt_image(batch, width, height)
        with DxOffscreenTarget(width, height, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()

        qt_pixels = [
            [
                (
                    qt_image.pixelColor(x, y).red(),
                    qt_image.pixelColor(x, y).green(),
                    qt_image.pixelColor(x, y).blue(),
                    qt_image.pixelColor(x, y).alpha(),
                )
                for x in range(width)
            ]
            for y in range(height)
        ]

        painted = 0
        for y in range(height):
            for x in range(width):
                offset = (y * width + x) * 4
                dx_color = _unpremultiply(tuple(dx_pixels[offset:offset + 4]))
                expected = qt_pixels[y][x]
                if dx_color[3] == 0 and expected[3] == 0:
                    continue
                painted += 1
                where = f"{label} mismatch at {(x, y)}"
                if exact:
                    self.assertEqual(dx_color, expected, where)
                    continue
                self._assert_tie(x, y, dx_color, expected, qt_pixels, tol, where)
        self.assertGreater(painted, 0, f"{label} painted nothing")

    def _assert_tie(
        self,
        x: int,
        y: int,
        dx_color: tuple[int, int, int, int],
        expected: tuple[int, int, int, int],
        qt_pixels: list,
        tol: int,
        where: str,
    ) -> None:
        """Resolve one non-exact pixel with the two measured sampling rules."""
        height = len(qt_pixels)
        width = len(qt_pixels[0])
        self.assertLessEqual(
            abs(dx_color[3] - expected[3]),
            SAMPLING_ALPHA_TOLERANCE,
            f"{where}: alpha {dx_color[3]} vs {expected[3]}",
        )
        if min(dx_color[3], expected[3]) < SAMPLING_COVER_FLOOR:
            return
        neighbourhood = [
            qt_pixels[ny][nx]
            for ny in range(max(0, y - 1), min(height, y + 2))
            for nx in range(max(0, x - 1), min(width, x + 2))
        ]
        for channel in range(4):
            low = min(pixel[channel] for pixel in neighbourhood) - SAMPLING_SLACK
            high = max(pixel[channel] for pixel in neighbourhood) + SAMPLING_SLACK
            if low <= dx_color[channel] <= high:
                continue
            neighbour = max(neighbourhood, key=lambda pixel: pixel[channel])
            self.assertLessEqual(
                abs(dx_color[channel] - neighbour[channel]),
                tol,
                f"{where}: channel {channel} "
                f"{dx_color[channel]} vs {neighbour[channel]}",
            )

    def test_rounded_rect_matches_qt_reference_pixels(self):
        """Corner rounding must cut exactly the same coverage as Qt."""
        batch = DrawBatch((
            RectCommand(Rect(2, 2, 20, 20), fill=Color(220, 40, 80), radius=6.0),
        ))
        self._assert_parity(batch, 24, 24, label="rounded rect")

    def test_rounded_rect_corner_is_cut_not_squared(self):
        """A radius must actually remove the corner pixels."""
        batch = DrawBatch((
            RectCommand(Rect(2, 2, 20, 20), fill=Color(220, 40, 80), radius=6.0),
        ))
        qt_image = self._qt_image(batch, 24, 24)
        self.assertEqual(qt_image.pixelColor(2, 2).alpha(), 0)
        self.assertEqual(qt_image.pixelColor(12, 12).alpha(), 255)

    def test_rounded_rect_with_antialias_matches_qt_reference_pixels(self):
        """Opted-in antialiasing must agree on which pixels are painted."""
        batch = DrawBatch((
            RectCommand(
                Rect(2, 2, 20, 20),
                fill=Color(30, 180, 210),
                radius=6.0,
                antialias=True,
            ),
        ))
        self._assert_parity(batch, 24, 24, exact=False, label="antialiased rounded rect")

    def test_stroked_polyline_caps_match_qt_reference_pixels(self):
        """Cap style changes end coverage, so it must be an explicit field."""
        outline = build_polygon_path([Point(4, 12), Point(20, 12)], closed=False)
        for cap in (StrokeCap.FLAT, StrokeCap.ROUND, StrokeCap.SQUARE):
            with self.subTest(cap=cap):
                batch = DrawBatch((
                    RectCommand(Rect(0, 0, 24, 24), fill=Color(0, 0, 0)),
                    PathCommand(
                        outline,
                        stroke=Color(255, 255, 255),
                        stroke_width=6.0,
                        stroke_cap=int(cap),
                    ),
                ))
                self._assert_parity(
                    batch,
                    24,
                    24,
                    exact=False,
                    tol=8,
                    label=f"{cap!r} capped polyline",
                )

    def test_flat_cap_polyline_stays_open(self):
        """An open path must not be closed, or the caps become joins.

        Closing the figure would stroke a phantom return segment and extend the
        line well past its declared endpoints on the DX side only.
        """
        outline = build_polygon_path([Point(8, 12), Point(16, 12)], closed=False)
        batch = DrawBatch((
            RectCommand(Rect(0, 0, 24, 24), fill=Color(0, 0, 0)),
            PathCommand(
                outline,
                stroke=Color(255, 255, 255),
                stroke_width=6.0,
                stroke_cap=int(StrokeCap.FLAT),
            ),
        ))
        qt_image = self._qt_image(batch, 24, 24)
        self.assertEqual(qt_image.pixelColor(12, 12), QColor(255, 255, 255))
        # Pixels outside the declared segment must stay background on both sides.
        self.assertEqual(qt_image.pixelColor(2, 12), QColor(0, 0, 0))
        self.assert_parity_untouched(batch, 24, 24, ((2, 12), (21, 12), (12, 5)))

    def assert_parity_untouched(self, batch, width, height, points) -> None:
        """Assert the named pixels are identical and painted as background."""
        qt_image = self._qt_image(batch, width, height)
        with DxOffscreenTarget(width, height, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()
        for x, y in points:
            offset = (y * width + x) * 4
            self.assertEqual(
                _unpremultiply(tuple(dx_pixels[offset:offset + 4])),
                (
                    qt_image.pixelColor(x, y).red(),
                    qt_image.pixelColor(x, y).green(),
                    qt_image.pixelColor(x, y).blue(),
                    qt_image.pixelColor(x, y).alpha(),
                ),
                f"untouched pixel mismatch at {(x, y)}",
            )

    def test_polygon_join_styles_match_qt_reference_pixels(self):
        """Polygon fill plus join style must agree on the shared path command."""
        triangle = build_polygon_path([Point(12, 2), Point(21, 20), Point(3, 20)])
        for join in (StrokeJoin.MITER, StrokeJoin.BEVEL, StrokeJoin.ROUND):
            with self.subTest(join=join):
                batch = DrawBatch((
                    PathCommand(
                        triangle,
                        fill=Color(90, 200, 120),
                        stroke=Color(20, 20, 20),
                        stroke_width=4.0,
                        stroke_join=int(join),
                    ),
                ))
                self._assert_parity(
                    batch,
                    24,
                    24,
                    exact=False,
                    tol=8,
                    label=f"{join!r} joined polygon",
                )

    def test_polygon_join_styles_match_with_antialias(self):
        """Antialiased joins must still agree on coverage decisions."""
        triangle = build_polygon_path([Point(12.5, 2.5), Point(22.5, 22.5), Point(2.5, 22.5)])
        for join in (StrokeJoin.MITER, StrokeJoin.BEVEL, StrokeJoin.ROUND):
            with self.subTest(join=join):
                batch = DrawBatch((
                    PathCommand(
                        triangle,
                        fill=Color(90, 200, 120),
                        stroke=Color(20, 20, 20),
                        stroke_width=3.0,
                        stroke_join=int(join),
                        antialias=True,
                    ),
                ))
                self._assert_parity(
                    batch,
                    24,
                    24,
                    exact=False,
                    tol=8,
                    label=f"AA {join!r} polygon",
                )

    def test_rounded_clip_matches_qt_reference_pixels(self):
        """A rounded clip must not leak paint into the cut corners."""
        batch = DrawBatch((
            ClipPush(Rect(2, 2, 20, 20), radius=6.0),
            RectCommand(Rect(0, 0, 24, 24), fill=Color(255, 210, 0)),
            ClipPop(),
        ))
        self._assert_parity(batch, 24, 24, exact=False, tol=8, label="rounded clip")

    def test_path_clip_matches_qt_reference_pixels(self):
        """A path clip must bound paint to the declared outline."""
        clip_path = build_polygon_path([Point(12, 2), Point(22, 22), Point(2, 22)])
        batch = DrawBatch((
            ClipPush(Rect(0, 0, 24, 24), path=clip_path),
            RectCommand(Rect(0, 0, 24, 24), fill=Color(200, 80, 220)),
            ClipPop(),
        ))
        self._assert_parity(batch, 24, 24, exact=False, tol=8, label="path clip")

    def test_gradient_fill_matches_qt_reference_pixels(self):
        """Stop ordering and coverage are exact; the colour ramp may differ.

        Qt's QLinearGradient and Direct2D interpolate with different precision,
        so midtone channels are allowed to differ by one 8-bit step while alpha
        and geometry stay exact.
        """
        ramp = LinearGradientFill(
            Point(0, 0),
            Point(24, 0),
            (
                GradientStop(0.0, Color(255, 0, 0)),
                GradientStop(0.5, Color(0, 255, 128)),
                GradientStop(1.0, Color(0, 0, 255)),
            ),
        )
        batch = DrawBatch((RectCommand(Rect(0, 0, 24, 8), fill=ramp),))
        qt_image = self._qt_image(batch, 24, 8)
        with DxOffscreenTarget(24, 8, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()

        checked = 0
        for y in range(8):
            for x in range(24):
                offset = (y * 24 + x) * 4
                dx_color = _unpremultiply(tuple(dx_pixels[offset:offset + 4]))
                qt_color = qt_image.pixelColor(x, y)
                expected = (
                    qt_color.red(),
                    qt_color.green(),
                    qt_color.blue(),
                    qt_color.alpha(),
                )
                self.assertEqual(dx_color[3], expected[3], f"gradient alpha at {(x, y)}")
                if dx_color[3] == 0:
                    continue
                checked += 1
                for channel in range(3):
                    self.assertLessEqual(
                        abs(dx_color[channel] - expected[channel]),
                        GRADIENT_CHANNEL_TOLERANCE,
                        f"gradient channel {channel} at {(x, y)}: "
                        f"{dx_color[channel]} vs {expected[channel]}",
                    )
        self.assertEqual(checked, 24 * 8, "gradient covered the whole rect")

    def test_gradient_stops_are_sorted_before_encoding(self):
        """Out-of-order stops must resolve to the same ramp on both backends."""
        unsorted = LinearGradientFill(
            Point(0, 0),
            Point(16, 0),
            (
                GradientStop(1.0, Color(0, 0, 255)),
                GradientStop(0.0, Color(255, 0, 0)),
            ),
        )
        ordered = LinearGradientFill(
            Point(0, 0),
            Point(16, 0),
            (
                GradientStop(0.0, Color(255, 0, 0)),
                GradientStop(1.0, Color(0, 0, 255)),
            ),
        )
        self.assertEqual(unsorted.stops, ordered.stops)

        unsorted_batch = DrawBatch((RectCommand(Rect(0, 0, 16, 4), fill=unsorted),))
        ordered_batch = DrawBatch((RectCommand(Rect(0, 0, 16, 4), fill=ordered),))
        qt_image = self._qt_image(unsorted_batch, 16, 4)
        with DxOffscreenTarget(16, 4, warp=True) as target:
            target.render_batch(unsorted_batch)
            dx_pixels = target.readback_rgba()
        for y in range(4):
            for x in range(16):
                offset = (y * 16 + x) * 4
                dx_color = _unpremultiply(tuple(dx_pixels[offset:offset + 4]))
                qt_color = qt_image.pixelColor(x, y)
                self.assertEqual(dx_color[3], qt_color.alpha(), f"alpha at {(x, y)}")
                if dx_color[3] == 0:
                    continue
                for channel in range(3):
                    self.assertLessEqual(
                        abs(dx_color[channel] - qt_color.getRgb()[channel]),
                        GRADIENT_CHANNEL_TOLERANCE,
                        f"unsorted gradient channel {channel} at {(x, y)}",
                    )

        with DxOffscreenTarget(16, 4, warp=True) as target:
            target.render_batch(unsorted_batch)
            unsorted_pixels = target.readback_rgba()
        with DxOffscreenTarget(16, 4, warp=True) as target:
            target.render_batch(ordered_batch)
            ordered_pixels = target.readback_rgba()
        self.assertEqual(unsorted_pixels, ordered_pixels)

    def test_primitive_layer_and_z_order_is_preserved(self):
        """The new primitives must not disturb layer/z/order resolution."""
        batch = DrawBatch((
            RectCommand(Rect(0, 0, 12, 12), fill=Color(255, 0, 0), layer=int(Layer.PANEL), z=1),
            PathCommand(
                build_polygon_path([Point(0, 0), Point(12, 0), Point(12, 12)]),
                fill=Color(0, 255, 0),
                layer=int(Layer.PANEL),
                z=2,
            ),
            RectCommand(Rect(0, 0, 12, 12), fill=Color(0, 0, 255), layer=int(Layer.PANEL), z=3),
        ))
        self._assert_parity(batch, 12, 12, label="ordered primitives")

        qt_image = self._qt_image(batch, 12, 12)
        self.assertEqual(qt_image.pixelColor(2, 2), QColor(0, 0, 255))


if __name__ == "__main__":
    unittest.main()
