"""竖向频段滑条：单块几何、中心 ±10Hz 吸附、命中判定、Qt 宿主与按钮组摆放。"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from lib.core.event.center import EventType, cleanup_event_center, get_event_center
from lib.core.graphics.panel_visuals import SLIDER_HANDLE_ASPECT
from lib.core.graphics.speaker_band_visuals import (
    BAND_SLIDER_GAP,
    BAND_SLIDER_WIDTH,
    band_hit_test,
    band_position,
    band_ratio_at,
    band_track_rect,
    build_band_slider_visual,
)
from lib.core.graphics.speaker_visuals import (
    SPEAKER_SEARCH_Y,
    build_speaker_search_visual,
    speaker_visual_hit_test,
)
from lib.core.speaker_band import (
    BAND_HALF_WIDTH_HZ,
    BAND_SNAP_HZ,
    band_center_hz,
    band_from_center_hz,
    band_from_center_ratio,
    band_max_hz,
    band_min_hz,
    clear_speaker_bands,
    default_band,
    get_speaker_band,
    set_speaker_band,
)
from lib.script.ui.speaker_band_slider import (
    DEFAULT_HEIGHT as BAND_SLIDER_HEIGHT,
    SpeakerBandSlider,
)


class _Metrics:
    default_font = None

    def measure(self, text, *, digit=False, side=False) -> float:
        return float(len(str(text)) * 7)


def _speaker(backend: str = 'qt', instance: int = 7):
    return SimpleNamespace(backend_id=backend, instance_id=instance, object_type='speaker')


class BandSliderVisualTests(unittest.TestCase):
    def test_slider_keeps_the_shared_width_and_draws_one_block(self):
        visual = build_band_slider_visual(band=(60.0, 250.0), height=124)

        self.assertEqual((visual.size.width, visual.size.height), (BAND_SLIDER_WIDTH, 124))
        self.assertEqual(
            visual.track_rect,
            band_track_rect(width=BAND_SLIDER_WIDTH, height=124),
        )
        self.assertEqual(len(visual.handle_rects), 1)
        self.assertEqual(visual.center_rect.width, visual.track_rect.width)
        self.assertAlmostEqual(
            visual.center_rect.width / visual.center_rect.height,
            SLIDER_HANDLE_ASPECT,
            places=2,
        )

    def test_block_sits_inside_the_filled_band(self):
        visual = build_band_slider_visual(band=band_from_center_hz(120.0), height=124)

        block_center = visual.center_rect.y + visual.center_rect.height / 2
        band_top = visual.band_rect.y
        band_bottom = visual.band_rect.y + visual.band_rect.height
        self.assertGreaterEqual(block_center, band_top - 1)
        self.assertLessEqual(block_center, band_bottom + 1)

    def test_handle_stays_inside_the_track_at_both_extremes(self):
        for center in (1.0, 100000.0):
            visual = build_band_slider_visual(band=band_from_center_hz(center), height=124)
            bottom = visual.track_rect.y + visual.track_rect.height
            for handle in visual.handle_rects:
                self.assertGreaterEqual(handle.y, visual.track_rect.y)
                self.assertLessEqual(handle.y + handle.height, bottom)

    def test_ratio_and_position_are_inverses(self):
        visual = build_band_slider_visual(band=(60.0, 250.0), height=140)
        for ratio in (0.0, 0.3, 1.0):
            y = band_position(visual.track_rect, ratio)
            self.assertAlmostEqual(band_ratio_at(visual.track_rect, y), ratio, places=6)

    def test_ratio_at_the_ends_is_clamped(self):
        visual = build_band_slider_visual(band=(60.0, 250.0), height=140)
        track = visual.track_rect
        self.assertEqual(band_ratio_at(track, track.y - 500), 1.0)
        self.assertEqual(band_ratio_at(track, track.y + track.height + 500), 0.0)

    def test_hit_test_accepts_anywhere_on_the_bar(self):
        visual = build_band_slider_visual(band=(60.0, 250.0), height=140)
        middle_x = visual.track_rect.x + visual.track_rect.width / 2

        for spot in (0.1, 0.5, 0.9):
            y = visual.track_rect.y + visual.track_rect.height * spot
            self.assertEqual(
                band_hit_test(visual.track_rect, visual.center_rect, middle_x, y),
                'band',
            )

    def test_hit_test_ignores_pointers_outside_the_bar(self):
        visual = build_band_slider_visual(band=(60.0, 250.0), height=140)
        track = visual.track_rect
        self.assertEqual(
            band_hit_test(visual.track_rect, visual.center_rect,
                          track.x - 200, track.y + 10),
            '',
        )
        self.assertEqual(
            band_hit_test(visual.track_rect, visual.center_rect,
                          track.x + 2, track.y + track.height + 200),
            '',
        )

    def test_dragging_through_ratios_always_yields_ten_hertz_bands(self):
        """拖动换算出来的频段总是整 10Hz 中心、固定 ±10Hz 宽。"""
        for ratio in (0.0, 0.21, 0.5, 0.77, 1.0):
            band = band_from_center_ratio(ratio)
            center = band_center_hz(band)
            self.assertAlmostEqual(band[1] - band[0], 2 * BAND_HALF_WIDTH_HZ, places=6)
            self.assertAlmostEqual(center % BAND_SNAP_HZ, 0.0, places=6)


class SpeakerMenuBandLayoutTests(unittest.TestCase):
    def _visual(self, **kwargs):
        return build_speaker_search_visual('', '', (), _Metrics(), **kwargs)

    def test_band_slider_sits_right_of_the_menu_block(self):
        visual = self._visual()

        self.assertEqual(visual.band_rect.x, visual.volume_rect.width + BAND_SLIDER_GAP)
        self.assertEqual(visual.band_rect.width, BAND_SLIDER_WIDTH)
        self.assertEqual(visual.band_rect.y, 0)
        self.assertEqual(
            visual.band_rect.y + visual.band_rect.height,
            visual.search_rect.y + visual.search_rect.height,
        )
        self.assertEqual(
            visual.band_rect.height, SPEAKER_SEARCH_Y + visual.search_rect.height,
        )

    def test_window_is_wide_enough_for_the_band_slider(self):
        visual = self._visual()
        self.assertGreaterEqual(
            visual.size.width, visual.band_rect.x + visual.band_rect.width,
        )

    def test_hit_test_distinguishes_the_band_block_from_the_volume_row(self):
        visual = self._visual(band=(60.0, 250.0))
        middle_x = visual.band_track_rect.x + visual.band_track_rect.width / 2
        block = visual.band_center_rect
        fill_top = visual.band_fill_rect.y
        fill_bottom = visual.band_fill_rect.y + visual.band_fill_rect.height
        # 频段填充的上下沿都在滑条内，按哪儿都应该落在块上。
        for y in (fill_top + 1, fill_bottom - 1, block.y + block.height / 2):
            self.assertEqual(
                speaker_visual_hit_test(visual, middle_x, y),
                ('band', -1),
            )
        self.assertEqual(
            speaker_visual_hit_test(visual, visual.volume_rect.x + 2, visual.volume_rect.y + 2),
            ('volume', -1),
        )

    def test_band_hit_area_does_not_steal_result_rows(self):
        """结果框比菜单宽时，频段滑条的命中区不能压到第一行结果上。"""
        long_title = "很长的歌曲名字" * 6
        visual = build_speaker_search_visual('', '', (long_title,), _Metrics())
        first_row = visual.result_rects[0]
        self.assertGreater(first_row.x + first_row.width, visual.band_rect.x)
        self.assertEqual(
            speaker_visual_hit_test(
                visual, visual.band_rect.x + visual.band_rect.width / 2, first_row.y + 2,
            )[0],
            'result',
        )

    def test_band_argument_moves_the_block(self):
        narrow = self._visual(band=band_from_center_hz(120.0))
        wide = self._visual(band=band_from_center_hz(1200.0))
        self.assertNotEqual(narrow.band_center_rect, wide.band_center_rect)
        self.assertNotEqual(narrow.band_fill_rect, wide.band_fill_rect)


class SpeakerBandSliderWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        cleanup_event_center()
        clear_speaker_bands()
        self.addCleanup(clear_speaker_bands)
        self.addCleanup(cleanup_event_center)

    def _slider(self) -> SpeakerBandSlider:
        slider = SpeakerBandSlider()
        self.addCleanup(slider.deleteLater)
        slider.show()
        self.app.processEvents()
        return slider

    @staticmethod
    def _mouse(kind, widget, x: float, y: float) -> QMouseEvent:
        return QMouseEvent(
            kind,
            QPointF(x, y),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

    def test_unbound_slider_shows_the_default_band(self):
        slider = self._slider()
        slider.set_speaker(None)
        self.assertEqual(slider.band, default_band())

    def test_binding_a_speaker_reads_its_own_band(self):
        set_speaker_band('qt', 7, 120.0, 400.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))
        self.assertEqual(slider.band, get_speaker_band('qt', 7))

    def test_dragging_the_block_writes_a_snapped_ten_hertz_band_back(self):
        set_speaker_band('qt', 7, 60.0, 250.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))

        track = slider._ensure_visual().track_rect
        slider._dragging = 'band'
        slider._apply_y(track.y + track.height * 0.75)

        band = get_speaker_band('qt', 7)
        # 拖动后上下限固定相差 ±10Hz，中心吸附到 10Hz 整数倍。
        self.assertAlmostEqual(band[1] - band[0], 2 * BAND_HALF_WIDTH_HZ, places=6)
        self.assertAlmostEqual(band_center_hz(band) % BAND_SNAP_HZ, 0.0, places=6)
        self.assertNotAlmostEqual(band[0], 60.0, places=3)

    def test_dragging_to_the_top_stays_inside_the_slider(self):
        set_speaker_band('qt', 7, 60.0, 250.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))

        track = slider._ensure_visual().track_rect
        slider._dragging = 'band'
        slider._apply_y(track.y - 500)

        band = get_speaker_band('qt', 7)
        self.assertLess(band[0], band[1])
        self.assertGreaterEqual(band[0], band_min_hz())
        self.assertLessEqual(band[1], band_max_hz())

    def test_dragging_to_the_bottom_stays_inside_the_slider(self):
        set_speaker_band('qt', 7, 60.0, 250.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))

        track = slider._ensure_visual().track_rect
        slider._dragging = 'band'
        slider._apply_y(track.y + track.height + 500)

        band = get_speaker_band('qt', 7)
        self.assertGreaterEqual(band[0], band_min_hz())
        self.assertLessEqual(band[1], band_max_hz())

    def test_press_on_the_bar_starts_a_block_drag(self):
        set_speaker_band('qt', 7, 40.0, 900.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))

        visual = slider._ensure_visual()
        middle_x = visual.track_rect.x + visual.track_rect.width / 2
        block = visual.center_rect
        y = block.y + block.height / 2
        slider.mousePressEvent(self._mouse(QEvent.MouseButtonPress, slider, middle_x, y))
        self.assertEqual(slider._dragging, 'band')
        slider.mouseReleaseEvent(self._mouse(QEvent.MouseButtonRelease, slider, middle_x, y))
        self.assertEqual(slider._dragging, '')

    def test_press_outside_the_bar_does_not_start_a_drag(self):
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))
        visual = slider._ensure_visual()
        slider.mousePressEvent(self._mouse(
            QEvent.MouseButtonPress, slider, visual.track_rect.x - 50,
            visual.track_rect.y + 2,
        ))
        self.assertEqual(slider._dragging, '')

    def test_release_publishes_the_band_bubble(self):
        set_speaker_band('qt', 7, 60.0, 250.0)
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))
        seen: list[str] = []
        get_event_center().subscribe(
            EventType.INFORMATION, lambda event: seen.append(str(event.data.get('text', ''))),
        )

        visual = slider._ensure_visual()
        middle_x = visual.track_rect.x + visual.track_rect.width / 2
        block = visual.center_rect
        y = block.y + block.height / 2
        slider.mousePressEvent(self._mouse(QEvent.MouseButtonPress, slider, middle_x, block.y + 20))
        slider.mouseMoveEvent(self._mouse(QEvent.MouseMove, slider, middle_x, y))
        slider.mouseReleaseEvent(self._mouse(QEvent.MouseButtonRelease, slider, middle_x, y))

        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith('响应频段'))

    def test_fade_in_resyncs_the_band_from_the_registry(self):
        slider = self._slider()
        slider.set_speaker(_speaker('qt', 7))
        set_speaker_band('qt', 7, 200.0, 800.0)
        slider.fade_in()
        self.assertTrue(slider.is_visible)
        self.assertEqual(slider.band, get_speaker_band('qt', 7))


class SpeakerControlButtonsBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        clear_speaker_bands()
        self.addCleanup(clear_speaker_bands)

    def test_band_slider_is_placed_right_of_the_search_row(self):
        from lib.script.ui.speaker_control_buttons import SpeakerControlButtons

        buttons = SpeakerControlButtons(SimpleNamespace())
        try:
            buttons._anchor_point = QPoint(100, 400)
            buttons._anchor_available = True
            buttons._update_positions()

            slider = buttons._band_slider
            search_height = BAND_SLIDER_HEIGHT - SPEAKER_SEARCH_Y
            self.assertEqual(slider.x(), 100 + buttons._volume_slider.width() + BAND_SLIDER_GAP)
            self.assertEqual(slider.width(), BAND_SLIDER_WIDTH)
            self.assertEqual(slider.height(), BAND_SLIDER_HEIGHT)
            # 顶端对齐第一行按钮，底端对齐搜索框下沿
            self.assertEqual(slider.y(), buttons._play_pause_btn.y())
            self.assertEqual(slider.y() + slider.height(), 400 + search_height)
        finally:
            buttons.cleanup()

    def test_focused_speaker_is_forwarded_to_the_slider(self):
        from lib.script.ui.speaker_control_buttons import SpeakerControlButtons

        buttons = SpeakerControlButtons(SimpleNamespace())
        speaker = _speaker('qt', 9)
        try:
            buttons.set_focused_speaker(speaker)
            self.assertIs(buttons._band_slider.bound_speaker, speaker)
        finally:
            buttons.cleanup()

    def test_fade_in_and_out_move_the_band_slider_with_the_menu(self):
        from lib.script.ui.speaker_control_buttons import SpeakerControlButtons

        buttons = SpeakerControlButtons(SimpleNamespace())
        try:
            buttons.fade_in()
            self.assertTrue(buttons._band_slider.is_visible)
            buttons.fade_out()
            self.assertFalse(buttons._band_slider.is_visible)
        finally:
            buttons.cleanup()


if __name__ == '__main__':
    unittest.main()
