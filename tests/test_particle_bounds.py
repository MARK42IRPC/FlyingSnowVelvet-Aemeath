import unittest
from types import SimpleNamespace

from lib.core.qt_particle_system import _particle_bounds


class ParticleBoundsTests(unittest.TestCase):
    def test_circle_bounds_use_radius_and_motion_history(self):
        particle = SimpleNamespace(
            x=24.0,
            y=36.0,
            _render_x=20.0,
            _render_y=30.0,
            _tick_prev_x=10.0,
            _tick_prev_y=15.0,
            size=5.0,
            is_circle=True,
        )

        bounds = _particle_bounds(particle)

        self.assertEqual((bounds.left(), bounds.top()), (5.0, 10.0))
        self.assertEqual((bounds.right(), bounds.bottom()), (29.0, 41.0))

    def test_line_bounds_include_endpoint_and_pen_width(self):
        particle = SimpleNamespace(
            x=10.0,
            y=20.0,
            length=30.0,
            line_dx=0.6,
            line_dy=-0.8,
            pen_width=3.0,
            is_line=True,
        )

        bounds = _particle_bounds(particle)

        self.assertEqual((bounds.left(), bounds.top()), (7.0, -7.0))
        self.assertEqual((bounds.right(), bounds.bottom()), (31.0, 23.0))

    def test_text_bounds_include_baseline_and_bloom(self):
        particle = SimpleNamespace(
            x=100.0,
            y=80.0,
            _text_w=40.0,
            _text_h=18.0,
            _baseline_offset=4.0,
            bloom=6.0,
            is_text=True,
        )

        bounds = _particle_bounds(particle)

        self.assertEqual((bounds.left(), bounds.top()), (74.0, 60.0))
        self.assertEqual((bounds.right(), bounds.bottom()), (126.0, 90.0))


if __name__ == "__main__":
    unittest.main()
