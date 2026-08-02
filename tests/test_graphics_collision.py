import unittest

from lib.core.graphics.collision import (
    adjust_rect,
    point_in_rect,
    rects_intersect,
    segment_intersects_rect,
)
from lib.core.graphics.types import Point, Rect


class GraphicsCollisionTests(unittest.TestCase):
    def test_rect_intersection_requires_positive_overlap(self):
        self.assertTrue(rects_intersect(Rect(0, 0, 10, 10), Rect(9, 9, 4, 4)))
        self.assertFalse(rects_intersect(Rect(0, 0, 10, 10), Rect(10, 0, 4, 4)))

    def test_adjust_and_point_containment(self):
        expanded = adjust_rect(Rect(10, 20, 30, 40), -5, -6, 7, 8)

        self.assertEqual(expanded, Rect(5, 14, 42, 54))
        self.assertTrue(point_in_rect(Point(5, 14), expanded))
        self.assertFalse(point_in_rect(Point(4, 14), expanded))

    def test_segment_crossing_or_touching_rect_is_detected(self):
        bounds = Rect(10, 10, 20, 20)

        self.assertTrue(segment_intersects_rect(Point(0, 20), Point(40, 20), bounds))
        self.assertTrue(segment_intersects_rect(Point(0, 10), Point(10, 10), bounds))
        self.assertFalse(segment_intersects_rect(Point(0, 0), Point(5, 5), bounds))


if __name__ == "__main__":
    unittest.main()
