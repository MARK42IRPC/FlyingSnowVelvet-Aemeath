"""雪豹中心坐标必须保持 Qt 可接受的整数类型。"""

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

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication

from lib.core.event.center import cleanup_event_center
from lib.core.render.visuals.resources import ImageResource, RasterFrame
from lib.script.ui.world_objects.snow_leopard import SnowLeopard


def _resource() -> ImageResource:
    return ImageResource("snow-leopard-test", (RasterFrame(2, 2, bytes([255] * 16)),))


def _leopard() -> SnowLeopard:
    return SnowLeopard(
        position=QPoint(120, 240),
        size=(64, 48),
        visual_resource=_resource(),
    )


class SnowLeopardCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        cleanup_event_center()

    def test_get_center_accepts_fractional_physics_coordinates(self):
        leopard = _leopard()
        try:
            body = leopard._physics_body
            # 物理步进会把位置写成浮点数；QPoint 不接受 float。
            body.render_x = 120.5
            body.render_y = 240.25
            body.x = 120.5
            body.y = 240.25

            center = leopard.get_center()

            self.assertIsInstance(center, QPoint)
            self.assertEqual(center, QPoint(152, 234))
        finally:
            leopard.close()

    def test_click_paths_survive_fractional_frame_coordinates(self):
        leopard = _leopard()
        try:
            leopard._physics_body.render_y = 240.75
            leopard._physics_body.y = 240.75

            leopard._spawn_snow_drift_particles()
            leopard._spawn_snow_particles()
            leopard._spawn_burst_line_particles()
        finally:
            leopard.close()


if __name__ == "__main__":
    unittest.main()
