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

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QImage, QMouseEvent
from PyQt5.QtWidgets import QApplication

from lib.core.graphics.media_panel_visuals import (
    PROGRESS_PANEL_HEIGHT,
    slider_track_rect,
)
from lib.script.ui.forum_color_picker import (
    SLIDER_HEIGHT,
    ForumColorSlider,
    color_to_hsl,
    hsl_color,
    hue_gradient_stops,
)

#: 共享滑条把选中区间填成这个颜色，取色滑条上它只出现在被重画到顶层的把手上。
HANDLE_COLOR = "#ff95a4"


class ForumColorSliderGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slider_keeps_the_shared_progress_bar_height(self):
        slider = ForumColorSlider(tooltip="色相", gradient_stops=hue_gradient_stops())
        self.addCleanup(slider.deleteLater)
        self.assertEqual(SLIDER_HEIGHT, PROGRESS_PANEL_HEIGHT)
        self.assertEqual(slider.height(), PROGRESS_PANEL_HEIGHT)

    def test_track_rect_matches_the_shared_slider_geometry(self):
        slider = ForumColorSlider(tooltip="色相", gradient_stops=hue_gradient_stops())
        self.addCleanup(slider.deleteLater)
        slider.setFixedWidth(220)
        expected = slider_track_rect(width=220, height=slider.height())
        track = slider.track_rect()
        self.assertEqual(
            (track.x, track.y, track.width, track.height),
            (expected.x, expected.y, expected.width, expected.height),
        )

    def test_ratio_from_x_clamps_to_the_track(self):
        """旧版这里会 NameError：轨道几何引用了没有导入的 slider_track_rect。"""
        slider = ForumColorSlider(tooltip="色相", gradient_stops=hue_gradient_stops())
        self.addCleanup(slider.deleteLater)
        slider.setFixedWidth(220)
        track = slider.track_rect()
        self.assertAlmostEqual(slider.ratio_from_x(track.x + track.width / 2), 0.5, places=2)
        self.assertEqual(slider.ratio_from_x(track.x - 50), 0.0)
        self.assertEqual(slider.ratio_from_x(track.x + track.width + 50), 1.0)


class ForumColorSliderInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _slider(self) -> ForumColorSlider:
        slider = ForumColorSlider(tooltip="色相", gradient_stops=hue_gradient_stops())
        self.addCleanup(slider.deleteLater)
        slider.setFixedWidth(220)
        slider.show()
        self.app.processEvents()
        return slider

    @staticmethod
    def _mouse(kind, widget, x: float) -> QMouseEvent:
        return QMouseEvent(
            kind,
            QPointF(x, widget.height() / 2),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

    def test_press_maps_the_pointer_to_a_ratio(self):
        slider = self._slider()
        track = slider.track_rect()
        slider.mousePressEvent(self._mouse(QEvent.MouseButtonPress, slider, track.x + track.width * 0.25))
        self.assertAlmostEqual(slider.ratio, 0.25, places=2)
        slider.mouseReleaseEvent(self._mouse(QEvent.MouseButtonRelease, slider, track.x + track.width * 0.25))

    def test_drag_emits_every_change_and_release_keeps_the_last_ratio(self):
        slider = self._slider()
        track = slider.track_rect()
        seen: list[float] = []
        slider.valueChanged.connect(seen.append)
        # 先挪到别处，拖动才有「变化」可发：比例没变时滑条故意不发信号。
        slider.set_ratio(0.9)
        slider.mousePressEvent(self._mouse(QEvent.MouseButtonPress, slider, track.x))
        slider.mouseMoveEvent(self._mouse(QEvent.MouseMove, slider, track.x + track.width * 0.6))
        slider.mouseReleaseEvent(self._mouse(QEvent.MouseButtonRelease, slider, track.x + track.width))
        self.assertEqual(seen[0], 0.0)
        self.assertAlmostEqual(seen[1], 0.6, places=2)
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(slider.ratio, 1.0)

    def test_same_ratio_does_not_emit_twice(self):
        slider = self._slider()
        track = slider.track_rect()
        seen: list[float] = []
        slider.valueChanged.connect(seen.append)
        for _ in range(2):
            slider.mousePressEvent(
                self._mouse(QEvent.MouseButtonPress, slider, track.x + track.width * 0.5)
            )
            slider.mouseReleaseEvent(
                self._mouse(QEvent.MouseButtonRelease, slider, track.x + track.width * 0.5)
            )
        self.assertEqual(seen, [0.5])

    def test_right_button_leaves_the_ratio_alone(self):
        slider = self._slider()
        slider.set_ratio(0.4)
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(10.0, slider.height() / 2),
            Qt.RightButton,
            Qt.RightButton,
            Qt.NoModifier,
        )
        slider.mousePressEvent(event)
        self.assertAlmostEqual(slider.ratio, 0.4)


class ForumColorSliderPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _row(self, ratio: float, width: int = 220) -> tuple[ForumColorSlider, list[str]]:
        slider = ForumColorSlider(tooltip="色相", gradient_stops=hue_gradient_stops())
        self.addCleanup(slider.deleteLater)
        slider.setFixedWidth(width)
        slider.set_ratio(ratio)
        slider.show()
        self.app.processEvents()
        image = slider.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        track = slider.track_rect()
        y = int(track.y) + int(track.height) // 2
        colors = [
            image.pixelColor(x, y).name()
            for x in range(int(track.x), int(track.x) + int(track.width))
        ]
        return slider, colors

    def test_handle_is_painted_on_top_of_the_gradient(self):
        """渐变和压暗层曾经盖住把手，滑条看上去像没有把手。"""
        for ratio in (0.0, 0.35, 1.0):
            _slider, colors = self._row(ratio)
            self.assertIn(HANDLE_COLOR, colors, f"ratio={ratio} 找不到把手")

    def test_handle_sits_where_the_ratio_says(self):
        slider, colors = self._row(0.5)
        track = slider.track_rect()
        centers = [index for index, color in enumerate(colors) if color == HANDLE_COLOR]
        center = track.x + sum(centers) / len(centers)
        self.assertAlmostEqual(center, track.x + track.width * 0.5, delta=2.0)

    def test_unselected_tail_is_dimmed_but_keeps_the_ramp(self):
        _slider, colors = self._row(0.5)
        head = colors[10]
        tail = colors[-10]
        self.assertGreater(self._brightness(head), self._brightness(tail))
        self.assertNotEqual(tail, "#000000")

    @staticmethod
    def _brightness(color: str) -> int:
        return sum(int(color[index:index + 2], 16) for index in (1, 3, 5))


class ForumColorMathTests(unittest.TestCase):
    def test_hue_gradient_covers_the_whole_circle(self):
        stops = hue_gradient_stops()
        self.assertEqual(len(stops), 7)
        self.assertEqual(stops[0][0], 0.0)
        self.assertEqual(stops[-1][0], 1.0)
        self.assertEqual(stops[0][1], stops[-1][1])

    def test_color_round_trips_through_hsl(self):
        for hue in (0.0, 45.0, 200.0, 359.0):
            color = hsl_color(hue, 0.8)
            back_hue, back_lightness = color_to_hsl(color)
            self.assertAlmostEqual(back_hue, hue, delta=1.5)
            self.assertAlmostEqual(back_lightness, 0.8, delta=0.02)

    def test_lightness_stays_inside_the_visible_range(self):
        self.assertEqual(color_to_hsl("#000000")[1], 0.18)
        self.assertAlmostEqual(hsl_color(120.0, 0.0).lightnessF(), 0.18, places=3)


if __name__ == "__main__":
    unittest.main()
