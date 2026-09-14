from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication

from lib.core.event.center import cleanup_event_center, get_event_center, EventType
from lib.core.graphics.media_panel_visuals import (
    SLIDER_TICK_COUNT,
    build_slider_visual,
    slider_track_rect,
    snap_slider_ratio,
)
from lib.core.graphics.panel_visuals import SLIDER_HANDLE_ASPECT
from lib.core.graphics.speaker_visuals import (
    SPEAKER_CONTROL_HEIGHT,
    SPEAKER_SEARCH_Y,
    SPEAKER_VOLUME_Y,
    build_speaker_search_visual,
    speaker_visual_hit_test,
    speaker_volume_ratio_at,
)
from lib.script.ui.speaker_volume_slider import SpeakerVolumeSlider, snap_ratio


class _Metrics:
    default_font = None

    def measure(self, text, *, digit=False, side=False):
        return float(len(str(text)) * 7)


class _MusicService:
    def __init__(self, percent: int = 80) -> None:
        self.percent = percent
        self.published: list[float] = []

    def get_volume_percent(self) -> int:
        return self.percent

    def set_volume(self, value: float) -> None:
        self.percent = int(round(float(value) * 100))


class SliderVisualTests(unittest.TestCase):
    def test_slider_visual_has_twenty_ticks_and_portrait_handle(self):
        visual = build_slider_visual(ratio=0.5)
        expected_track = slider_track_rect(width=visual.size.width, height=visual.size.height)
        self.assertEqual(visual.track_rect, expected_track)
        self.assertEqual(len(visual.tick_rects), SLIDER_TICK_COUNT)
        self.assertEqual(visual.tick_rects[-1].x + visual.tick_rects[-1].width,
                         expected_track.x + expected_track.width)

        handle = visual.handle_rect
        self.assertGreater(handle.height, handle.width)
        self.assertAlmostEqual(handle.height / handle.width, SLIDER_HANDLE_ASPECT, places=2)
        self.assertGreaterEqual(handle.x, expected_track.x)
        self.assertLessEqual(handle.x + handle.width,
                             expected_track.x + expected_track.width)

    def test_handle_is_clamped_at_both_ends(self):
        empty = build_slider_visual(ratio=-1.0)
        self.assertEqual(empty.handle_rect.x, empty.track_rect.x)
        full = build_slider_visual(ratio=2.0)
        self.assertEqual(
            full.handle_rect.x + full.handle_rect.width,
            full.track_rect.x + full.track_rect.width,
        )

    def test_ratio_helpers_snap_to_tick_positions(self):
        self.assertAlmostEqual(snap_slider_ratio(0.53), 0.55)
        self.assertAlmostEqual(snap_slider_ratio(-3.0), 0.0)
        self.assertAlmostEqual(snap_slider_ratio(9.0), 1.0)
        track = slider_track_rect(width=240, height=20)
        self.assertAlmostEqual(speaker_volume_ratio_at(
            SimpleNamespace(volume_track_rect=track), track.x + track.width / 2
        ), 0.5)


class SpeakerVolumeVisualTests(unittest.TestCase):
    def test_volume_band_sits_between_controls_and_search_row(self):
        visual = build_speaker_search_visual("", "", (), _Metrics())
        self.assertEqual(visual.volume_rect.y, SPEAKER_VOLUME_Y)
        self.assertEqual(visual.search_rect.y, SPEAKER_SEARCH_Y)
        self.assertEqual(visual.search_rect.y,
                         visual.volume_rect.y + visual.volume_rect.height + 2)
        self.assertEqual(visual.volume_rect.x, 0)
        self.assertEqual(visual.volume_rect.width, visual.search_rect.x + visual.search_rect.width)
        controls_bottom = max(
            rect.y + rect.height for _name, rect in visual.control_rects
        )
        self.assertEqual(controls_bottom + 2, visual.volume_rect.y)
        self.assertEqual(controls_bottom, SPEAKER_CONTROL_HEIGHT * 2)

    def test_volume_band_is_hit_testable(self):
        visual = build_speaker_search_visual("", "", (), _Metrics(), volume=0.5)
        action, index = speaker_visual_hit_test(
            visual, visual.volume_rect.x + 2, visual.volume_rect.y + 2
        )
        self.assertEqual((action, index), ("volume", -1))
        self.assertAlmostEqual(
            speaker_volume_ratio_at(visual, visual.volume_track_rect.x),
            0.0,
        )


class SpeakerVolumeSliderWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        cleanup_event_center()
        self.service = _MusicService(80)
        self.service_patch = patch(
            "lib.script.ui.speaker_volume_slider.get_music_service",
            return_value=self.service,
        )
        self.service_patch.start()
        self.received: list[float] = []
        get_event_center().subscribe(
            EventType.MUSIC_VOLUME,
            lambda event: self.received.append(float(event.data["volume"])),
        )

    def tearDown(self) -> None:
        self.service_patch.stop()
        cleanup_event_center()

    def test_snap_ratio_rounds_to_fifths(self):
        self.assertAlmostEqual(snap_ratio(0.53), 0.55)
        self.assertAlmostEqual(snap_ratio(0.02), 0.0)
        self.assertAlmostEqual(snap_ratio(0.98), 1.0)

    def test_drag_publishes_snapped_volume_only_when_it_changes(self):
        slider = SpeakerVolumeSlider()
        try:
            track = slider.track_rect()
            slider.set_ratio(slider.ratio_from_x(track.x + track.width * 0.52), emit=True)
            self.assertEqual(self.received, [0.5])
            slider.set_ratio(slider.ratio_from_x(track.x + track.width * 0.52), emit=True)
            self.assertEqual(self.received, [0.5])
            slider.set_ratio(slider.ratio_from_x(track.x + track.width), emit=True)
            self.assertEqual(self.received, [0.5, 1.0])
        finally:
            slider.cleanup()

    def test_fade_in_syncs_ratio_from_music_service(self):
        slider = SpeakerVolumeSlider()
        try:
            slider.fade_in()
            self.assertTrue(slider.is_visible)
            self.assertAlmostEqual(slider.ratio, 0.8)
        finally:
            slider.cleanup()


class SpeakerControlButtonsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_volume_slider_sits_above_the_search_row_and_pushes_buttons_up(self):
        from lib.script.ui.speaker_control_buttons import SpeakerControlButtons

        buttons = SpeakerControlButtons(SimpleNamespace())
        try:
            buttons._anchor_point = QPoint(100, 400)
            buttons._anchor_available = True
            buttons._update_positions()

            slider = buttons._volume_slider
            self.assertEqual((slider.x(), slider.y()), (100, 378))
            self.assertEqual(slider.width(), buttons._playlist_btn.width() * 3)
            # 搜索优先级/播放列表贴住滑条下沿，其余按钮再上移一行
            self.assertEqual(buttons._search_priority_btn.y(), 378 - 2 - 32)
            self.assertEqual(buttons._playlist_btn.y(), 378 - 2 - 32)
            self.assertEqual(buttons._play_pause_btn.y(), 378 - 2 - 64)
            self.assertEqual(buttons._platform_mode_btn.y(), 378 - 2 - 64)
            self.assertEqual(buttons._playlist_btn.x(), 100 + 240 - 80)
        finally:
            buttons.cleanup()
