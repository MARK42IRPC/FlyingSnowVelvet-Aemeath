from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtWidgets import QApplication

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from config.config import PARTICLES
from lib.core.dx_bridge.offscreen import DxOffscreenTarget, find_dx_library
from lib.core.graphics.announcement_visuals import build_announcement_visual
from lib.core.graphics.application_visuals import (
    build_qr_panel_visual,
    create_portable_command_hint_metrics,
    qr_panel_size,
)
from lib.core.graphics.speaker_playlist_visuals import build_speaker_playlist_visual
from lib.core.graphics.speaker_visuals import build_speaker_search_visual
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.types import Color
from lib.core.graphics.visuals import build_command_shell_batch, build_particle_batch
from lib.core.layer import Layer
from lib.core.qt_bridge.draw_backend import QtDrawBackend


class _SquareParticle:
    alive = True
    x = 4.0
    y = 4.0
    life = 1.0
    max_life = 1.0
    size = 4.0
    color = Color(220, 40, 80)
    layer = Layer.PARTICLE
    z = 0
    _draw_order = 1


class _CircleParticle(_SquareParticle):
    x = 10.0
    is_circle = True
    size = 2.0
    color = Color(30, 180, 210)
    _draw_order = 2


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "Qt/DX parity requires Windows and a built DX DLL",
)
class VisualBackendParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _qt_image(batch, width: int, height: int) -> QImage:
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor("transparent"))
        painter = QPainter(image)
        try:
            QtDrawBackend().render(batch, painter)
        finally:
            painter.end()
        return image

    def _assert_sample_pixels(self, batch, width: int, height: int, points) -> None:
        qt_image = self._qt_image(batch, width, height)
        with DxOffscreenTarget(width, height, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()
        for x, y in points:
            offset = (int(y) * width + int(x)) * 4
            color = qt_image.pixelColor(int(x), int(y))
            self.assertEqual(
                tuple(dx_pixels[offset:offset + 4]),
                (color.red(), color.green(), color.blue(), color.alpha()),
                f"backend pixel mismatch at {(x, y)}",
            )

    def test_shared_particle_geometry_matches_qt_reference_pixels(self):
        with patch.dict(PARTICLES, {"enable_stroke": False}):
            batch = build_particle_batch([_SquareParticle(), _CircleParticle()])

        qt_image = self._qt_image(batch, 16, 8)

        with DxOffscreenTarget(16, 8, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()

        def dx_pixel(x: int, y: int) -> tuple[int, int, int, int]:
            offset = (y * 16 + x) * 4
            return tuple(dx_pixels[offset:offset + 4])

        for x, y in ((0, 0), (3, 3), (10, 4), (15, 7)):
            qt_color = qt_image.pixelColor(x, y)
            self.assertEqual(
                dx_pixel(x, y),
                (qt_color.red(), qt_color.green(), qt_color.blue(), qt_color.alpha()),
                f"backend pixel mismatch at {(x, y)}",
            )

    def test_command_shell_matches_qt_reference_pixels(self):
        batch = build_command_shell_batch(24, 16)
        qt_image = self._qt_image(batch, 24, 16)

        with DxOffscreenTarget(24, 16, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()

        for x, y in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (23, 15)):
            offset = (y * 24 + x) * 4
            qt_color = qt_image.pixelColor(x, y)
            self.assertEqual(
                tuple(dx_pixels[offset:offset + 4]),
                (qt_color.red(), qt_color.green(), qt_color.blue(), qt_color.alpha()),
                f"command shell pixel mismatch at {(x, y)}",
            )

    def test_qr_panel_geometry_matches_across_backends(self):
        width, height = qr_panel_size()
        frame = RasterFrame(2, 2, bytes((25, 50, 75, 255)) * 4)
        batch = build_qr_panel_visual(
            "扫码登录",
            "等待扫码",
            "加载中",
            ImageResource("qr:parity", (frame,)),
        ).batch
        qt_image = self._qt_image(batch, width, height)

        with DxOffscreenTarget(width, height, warp=True) as target:
            target.render_batch(batch)
            dx_pixels = target.readback_rgba()

        for x, y in ((0, 0), (2, 2), (4, 4), (width // 2, 180), (width - 1, height - 1)):
            offset = (y * width + x) * 4
            qt_color = qt_image.pixelColor(x, y)
            self.assertEqual(
                tuple(dx_pixels[offset:offset + 4]),
                (qt_color.red(), qt_color.green(), qt_color.blue(), qt_color.alpha()),
                f"QR panel pixel mismatch at {(x, y)}",
            )

    def test_speaker_search_visual_states_match_across_backends(self):
        visual = build_speaker_search_visual(
            "雪绒",
            "",
            tuple(f"03:2{i} 测试歌曲 {i}" for i in range(8)),
            create_portable_command_hint_metrics(),
            selected=1,
            hovered="search",
            pressed="search",
        )
        width, height = int(visual.size.width), int(visual.size.height)
        row = visual.result_rects[1]
        self._assert_sample_pixels(visual.batch, width, height, (
            (0, 0),
            (2, 2),
            (int(visual.input_rect.x), int(visual.input_rect.y)),
            (int(visual.search_rect.x + 5), int(visual.search_rect.y + 5)),
            (int(row.x + row.width - 3), int(row.y + row.height / 2)),
            (width - 1, height - 1),
        ))

    def test_speaker_playlist_visual_states_match_across_backends(self):
        visual = build_speaker_playlist_visual(
            tuple((index, f"歌曲 {index}") for index in range(9)),
            create_portable_command_hint_metrics(),
            current_index=1,
            selected=2,
            playing=True,
            progress=0.5,
            remaining=125,
            hovered="remove",
            pressed="remove",
        )
        width, height = int(visual.size.width), int(visual.size.height)
        row = visual.row_rects[2]
        self._assert_sample_pixels(visual.batch, width, height, (
            (0, 0),
            (2, 2),
            (int(visual.slider_rect.x + 3), int(visual.slider_rect.y + 3)),
            (int(row.x + row.width - 3), int(row.y + row.height / 2)),
            (int(visual.remove_rect.x + 5), int(visual.remove_rect.y + 5)),
            (width - 1, height - 1),
        ))

    def test_announcement_shell_matches_across_backends(self):
        visual = build_announcement_visual(
            create_portable_command_hint_metrics(),
            mode="error",
            hovered="retry",
            pressed="retry",
        )
        width, height = int(visual.size.width), int(visual.size.height)
        self._assert_sample_pixels(visual.batch, width, height, (
            (0, 0),
            (2, 2),
            (width // 2, 2),
            (2, height // 2),
            (width - 1, height - 1),
        ))


if __name__ == "__main__":
    unittest.main()
