import unittest
from unittest.mock import patch

from PyQt5.QtCore import QPoint, QRect

from lib.core import screen_utils
from lib.core.graphics.types import Point
from lib.core.qt_bridge import screen as qt_screen


class QtScreenBridgeTests(unittest.TestCase):
    def test_core_point_is_converted_at_qt_boundary(self):
        self.assertEqual(qt_screen._to_qpoint(Point(12.6, -3.4)), QPoint(13, -3))
        self.assertEqual(qt_screen._to_qpoint(QPoint(4, 5)), QPoint(4, 5))

    def test_legacy_clamp_facade_uses_core_algorithm_through_bridge(self):
        geometry = QRect(-200, 0, 200, 100)
        with patch.object(
            qt_screen,
            "get_screen_geometry_for_point",
            return_value=geometry,
        ):
            x, y, selected = screen_utils.clamp_rect_position(
                -250,
                90,
                50,
                30,
                point=Point(-100, 50),
            )

        self.assertEqual((x, y), (-200, 70))
        self.assertEqual(selected, geometry)


if __name__ == "__main__":
    unittest.main()
