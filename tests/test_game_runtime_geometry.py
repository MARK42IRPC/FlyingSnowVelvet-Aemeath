from __future__ import annotations

import unittest
from types import SimpleNamespace

from PyQt5.QtCore import QPoint, QRect

from lib.core.graphics.types import Rect
from lib.script.ui.game_runtime import (
    GameRuntime,
    GameRuntimePanel,
    aspect_resize_geometry,
    centered_aspect_rect,
)


class GameRuntimeGeometryTests(unittest.TestCase):
    def test_default_window_size_is_1000_by_800_pixels(self):
        self.assertEqual(GameRuntimePanel._DEFAULT_WIDTH, 1000)
        self.assertEqual(GameRuntimePanel._DEFAULT_HEIGHT, 800)

    def test_full_hd_container_centers_ten_by_eight_content(self):
        self.assertEqual(
            centered_aspect_rect(QRect(0, 0, 1920, 1080), 10, 8),
            QRect(285, 0, 1350, 1080),
        )

    def test_normal_panel_inset_keeps_ten_by_eight_content(self):
        self.assertEqual(
            centered_aspect_rect(QRect(0, 0, 1000, 800), 10, 8, 8),
            QRect(10, 8, 980, 784),
        )

    def test_corner_resize_uses_dominant_axis_and_keeps_ratio(self):
        start = QRect(100, 100, 1000, 800)
        enlarged = aspect_resize_geometry(start, {"right", "bottom"}, QPoint(120, 40), 600, 10, 8)
        self.assertEqual(enlarged, QRect(100, 100, 1120, 896))

        shrunk_from_left = aspect_resize_geometry(start, {"left"}, QPoint(180, 0), 600, 10, 8)
        self.assertEqual(shrunk_from_left, QRect(280, 100, 820, 656))

    def test_resize_respects_minimum_aspect_size(self):
        start = QRect(100, 100, 1000, 800)
        resized = aspect_resize_geometry(start, {"left", "top"}, QPoint(900, 900), 600, 10, 8)
        self.assertEqual(resized, QRect(500, 420, 600, 480))

    def test_runtime_exposes_middle_third_as_core_rect(self):
        runtime = GameRuntime.__new__(GameRuntime)
        runtime._panel = SimpleNamespace(
            get_game_middle_third_rect_global=lambda: QRect(40, 50, 300, 600),
        )

        result = runtime.get_lahai_game_middle_third_rect_global()

        self.assertIsInstance(result, Rect)
        self.assertEqual(result, Rect(40, 50, 300, 600))


if __name__ == "__main__":
    unittest.main()
