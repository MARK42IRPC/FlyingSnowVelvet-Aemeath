from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 与其余办公测试一致：指向临时用户根，避免读到本机的亮色主题配置。
_TEST_HOME = tempfile.mkdtemp(prefix="office-effort-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import (
    QApplication,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from lib.script.office.contracts import DEFAULT_REASONING_EFFORT, REASONING_EFFORTS
from lib.script.ui.office_effort_slider import (
    EFFORT_LABELS,
    EFFORT_LEVELS,
    EFFORT_STAR_COUNT,
    EFFORT_STARS,
    EFFORT_TICK_COUNT,
    OfficeEffortSlider,
    effort_level_color,
    effort_stars,
    star_dots,
)
from lib.script.ui.office_style import office_effort_colors, office_stylesheet
from lib.script.workbench.theme import get_workbench_colors


class OfficeEffortSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.slider = OfficeEffortSlider()
        # 刻度高度来自办公页 QSS，渲染前先挂进带主题样式的宿主，和真实页面一致。
        self._holder = QWidget()
        self._holder.setObjectName("OfficeWorkbenchPage")
        self._holder.setStyleSheet(office_stylesheet())
        holder_layout = QVBoxLayout(self._holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(self.slider)
        self._holder.show()
        self.addCleanup(self._holder.deleteLater)
        self.app.processEvents()

    def test_levels_follow_contract_order_with_five_names(self):
        self.assertEqual(len(EFFORT_LEVELS), 5)
        self.assertEqual([value for value, _label in EFFORT_LEVELS], list(REASONING_EFFORTS))
        self.assertEqual(EFFORT_LABELS, ("极速", "轻量", "一般", "思考", "沉思"))
        self.assertEqual(self.slider._slider.maximum(), 4)
        self.assertEqual(self.slider._slider.minimum(), 0)
        self.assertEqual(self.slider._slider.singleStep(), 1)
        self.assertEqual(self.slider._slider.pageStep(), 1)
        # 原生刻度在 QSS 覆盖 groove/handle 后不再绘制，五档刻度由滑条自绘。
        self.assertEqual(self.slider._slider.tickPosition(), QSlider.NoTicks)
        self.assertEqual(EFFORT_TICK_COUNT, 5)

    def test_default_level_is_balanced(self):
        self.assertEqual(self.slider.effort(), DEFAULT_REASONING_EFFORT)
        self.assertEqual(self.slider.level_name(), "一般")
        self.assertEqual(self.slider._level_label.text(), "一般")

    def test_level_text_and_property_follow_slider(self):
        self.slider._slider.setValue(4)
        self.assertEqual(self.slider.effort(), "ultra")
        self.assertEqual(self.slider._level_label.text(), "沉思")
        self.assertEqual(self.slider._level_label.property("level"), "4")
        self.assertEqual(self.slider._slider.property("level"), "4")
        self.assertIn("第 5/5 档", self.slider._level_label.toolTip())

    def test_moving_slider_emits_wire_value(self):
        seen: list[str] = []
        self.slider.effort_changed.connect(seen.append)

        self.slider._slider.setValue(1)

        self.assertEqual(seen, ["low"])

    def test_set_effort_is_silent_and_falls_back_to_default(self):
        seen: list[str] = []
        self.slider.effort_changed.connect(seen.append)

        self.slider.set_effort("max")
        self.slider.set_effort("ultra")
        self.slider.set_effort("unsupported")
        self.slider.set_effort("")

        self.assertEqual(seen, [])
        self.assertEqual(self.slider.effort(), DEFAULT_REASONING_EFFORT)
        self.assertEqual(self.slider.level_name(), "一般")

    def test_slider_ignores_wheel_events(self):
        event = Mock()
        self.slider._slider.wheelEvent(event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def _tick_band_colors(self, value: int) -> set[str]:
        """渲染滑条并收集刻度带（槽下方）出现过的颜色。"""
        slider = self.slider._slider
        slider.setValue(value)
        self._holder.resize(self._holder.sizeHint())
        self.app.processEvents()
        image = QImage(slider.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        slider.render(image)
        band = range(slider.height() // 2 + 6, slider.height())
        return {
            QColor(image.pixel(x, y)).name()
            for y in band
            for x in range(slider.width())
        }

    def test_ticks_are_painted_once_per_level_in_level_color(self):
        theme = get_workbench_colors()
        idle = QColor(theme.border_strong).name()
        ramp = office_effort_colors()

        lowest = self._tick_band_colors(0)

        # 第 1 档只有首个刻度点亮，其余四个是未到达分隔色。
        self.assertIn(ramp[0], lowest)
        self.assertIn(idle, lowest)
        self.assertNotIn(ramp[-1], lowest)

        highest = self._tick_band_colors(4)

        # 第 5 档五个刻度全部点亮，不再有未到达色。
        self.assertNotIn(idle, highest)
        for color in ramp:
            with self.subTest(color=color):
                self.assertIn(color, highest)

    def test_level_colors_run_from_pink_to_cyan(self):
        colors = office_effort_colors("dark")
        theme = get_workbench_colors("dark")

        self.assertEqual(len(colors), 5)
        self.assertEqual(colors[0], theme.pink)
        self.assertEqual(colors[-1], theme.cyan)
        self.assertEqual(colors, office_effort_colors())
        self.assertEqual(effort_level_color("off"), colors[0])
        self.assertEqual(effort_level_color("ultra"), colors[-1])

    def _star_columns(self, value: int) -> set[int]:
        """渲染滑条并收集填色段里亮点所在的列：星点是纯白，比档位色亮得多。"""
        slider = self.slider._slider
        slider.setValue(value)
        self._holder.resize(self._holder.sizeHint())
        self.app.processEvents()
        image = QImage(slider.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        slider.render(image)
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        groove = slider.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderGroove, slider
        )
        rows = range(max(0, groove.top()), min(slider.height(), groove.bottom() + 1))
        return {
            x
            for y in rows
            for x in range(slider.width())
            if min(QColor(image.pixel(x, y)).getRgb()[:3]) >= 200
        }

    def test_level_name_sits_left_of_the_track_on_the_same_row(self):
        label = self.slider._level_label
        track = self.slider._slider

        label_center = label.mapTo(self.slider, label.rect().center())
        track_center = track.mapTo(self.slider, track.rect().center())
        label_right = label.mapTo(self.slider, label.rect().topRight()).x()
        track_left = track.mapTo(self.slider, track.rect().topLeft()).x()

        # 档位名贴在滑条左边、和滑条同一行，不再居中悬浮在轨道上方。
        self.assertLess(label_right, track_left)
        self.assertLess(label_center.x(), track_center.x())
        self.assertLessEqual(abs(label_center.y() - track_center.y()), 3)

        track_top = track.mapTo(self.slider, track.rect().topLeft()).y()
        label_top = label.mapTo(self.slider, label.rect().topLeft()).y()
        self.assertLessEqual(label_top, track_top + track.height())

    def test_star_dots_stay_on_the_track_and_stop_at_the_filled_edge(self):
        self.assertEqual(star_dots(0.0, 200.0, 0.0, 10.0, 2.0), ())

        whole = star_dots(0.0, 200.0, 200.0, 10.0, 2.0)
        self.assertEqual(len(whole), EFFORT_STAR_COUNT)
        for x, y, radius, alpha in whole:
            with self.subTest(x=x):
                self.assertGreaterEqual(x, 0.0)
                self.assertLess(x, 200.0)
                self.assertLessEqual(abs(y - 10.0), 2.0)
                self.assertGreaterEqual(radius, 1.0)
                self.assertTrue(0 <= alpha <= 255)

        # 填色只到一半时少画一半：留下的星点位置和整条轨道时完全一致，不整体挪位。
        half = star_dots(0.0, 200.0, 100.0, 10.0, 2.0)
        self.assertEqual(
            [dot[0] for dot in half], [dot[0] for dot in whole if dot[0] < 100.0]
        )

    def test_starfield_is_painted_inside_the_track_and_grows_with_the_level(self):
        lowest = self._star_columns(0)
        highest = self._star_columns(4)

        self.assertTrue(highest)
        self.assertLess(len(lowest), len(highest))

    def test_star_map_is_fixed_by_seed(self):
        self.assertEqual(len(EFFORT_STARS), EFFORT_STAR_COUNT)
        self.assertEqual(EFFORT_STARS, effort_stars())
        for ratio, drift, radius, alpha in EFFORT_STARS:
            with self.subTest(ratio=ratio):
                self.assertGreaterEqual(ratio, 0.0)
                self.assertLessEqual(ratio, 1.0)
                self.assertLessEqual(abs(drift), 1.0)
                self.assertGreater(radius, 0.0)
                self.assertTrue(0 <= alpha <= 255)

    def test_level_colors_increase_monotonically_towards_cyan(self):
        def channels(value: str) -> tuple[int, int, int]:
            text = value.lstrip("#")
            return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))

        ramps = [channels(color) for color in office_effort_colors("dark")]

        red = [item[0] for item in ramps]
        blue = [item[2] for item in ramps]
        green = [item[1] for item in ramps]
        self.assertEqual(red, sorted(red, reverse=True))
        self.assertEqual(green, sorted(green))
        self.assertEqual(blue, sorted(blue))
        self.assertEqual(len(set(ramps)), 5)
