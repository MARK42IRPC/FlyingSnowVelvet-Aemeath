import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtCore import QRect

from lib.core.event.center import Event, EventType
from lib.core.qt_particle_system import (
    ParticleOverlay,
    _particle_bounds,
    _prepare_particles_for_inplace_update,
    _snapshot_particles_for_update,
    _translate_particle_coordinates,
    _update_particles_batch,
)


class ParticleBoundsTests(unittest.TestCase):
    @staticmethod
    def _overlay_stub(geometry, particles):
        class OverlayStub:
            def __init__(self):
                self._geometry = QRect(geometry)
                self._particles = particles
                self.geometry_changes = []

            def geometry(self):
                return QRect(self._geometry)

            def setGeometry(self, left, top, width, height):
                self._geometry = QRect(left, top, width, height)
                self.geometry_changes.append(QRect(self._geometry))

        return OverlayStub()

    def test_tick_interpolation_starts_from_logical_position(self):
        particle = SimpleNamespace(
            x=10.0,
            y=20.0,
            _render_x=10.5,
            _render_y=20.25,
        )

        _prepare_particles_for_inplace_update([particle])

        self.assertEqual((particle._tick_prev_x, particle._tick_prev_y), (10.0, 20.0))

    def test_reframe_translates_position_anchors_with_the_particle(self):
        particle = SimpleNamespace(
            x=10.0,
            y=20.0,
            _tick_prev_x=9.5,
            _tick_prev_y=19.5,
            _render_x=9.75,
            _render_y=19.75,
            cx=10.0,
            cy=20.0,
            _start_x=8.0,
            _start_y=18.0,
            _target_x=12.0,
            _target_y=22.0,
            _ground_y=300.0,
        )

        _translate_particle_coordinates(particle, 3.0, -4.0)

        self.assertEqual(
            (particle.x, particle.y, particle._tick_prev_x, particle._tick_prev_y),
            (13.0, 16.0, 12.5, 15.5),
        )
        self.assertEqual((particle.cx, particle.cy), (13.0, 16.0))
        self.assertEqual((particle._start_x, particle._start_y), (11.0, 14.0))
        self.assertEqual((particle._target_x, particle._target_y), (15.0, 18.0))
        self.assertEqual(particle._ground_y, 296.0)

    def test_reframe_translates_collision_screen_bounds(self):
        particle = SimpleNamespace(x=10.0, y=20.0, _screen_w=1920.0, _screen_h=1080.0)

        _translate_particle_coordinates(particle, -12.0, 7.0)

        self.assertEqual((particle.x, particle.y), (-2.0, 27.0))
        self.assertEqual((particle._screen_w, particle._screen_h), (1908.0, 1087.0))

    def test_reframe_keeps_stable_geometry_inside_edge_guard(self):
        particle = SimpleNamespace(
            x=60.0,
            y=70.0,
            _tick_prev_x=59.0,
            _tick_prev_y=69.0,
            _render_x=59.5,
            _render_y=69.5,
            size=6.0,
            life=1.0,
        )
        overlay = self._overlay_stub(QRect(300, 200, 120, 120), [particle])

        ParticleOverlay._reframe_overlay(overlay)

        self.assertEqual(overlay.geometry_changes, [])
        self.assertEqual((particle.x, particle.y), (60.0, 70.0))

    def test_reframe_preserves_particle_global_coordinates(self):
        particle = SimpleNamespace(
            x=4.0,
            y=5.0,
            _tick_prev_x=3.0,
            _tick_prev_y=4.0,
            _render_x=3.5,
            _render_y=4.5,
            size=6.0,
            life=1.0,
        )
        overlay = self._overlay_stub(QRect(300, 200, 120, 120), [particle])
        global_before = (
            overlay.geometry().x() + particle.x,
            overlay.geometry().y() + particle.y,
        )

        ParticleOverlay._reframe_overlay(overlay)

        self.assertEqual(len(overlay.geometry_changes), 1)
        self.assertEqual(
            (
                overlay.geometry().x() + particle.x,
                overlay.geometry().y() + particle.y,
            ),
            global_before,
        )

    def test_async_snapshot_update_does_not_mutate_rendered_particle(self):
        class MovingParticle:
            def __init__(self):
                self.x = 10.0
                self.y = 20.0
                self._render_x = 10.5
                self._render_y = 20.5
                self.life = 1.0

            def update(self):
                self.x += 1.0
                self.y += 2.0

            @property
            def alive(self):
                return self.life > 0.0

        particle = MovingParticle()

        snapshot = _snapshot_particles_for_update([particle])
        updated = _update_particles_batch(snapshot)

        self.assertIsNot(snapshot[0], particle)
        self.assertEqual((particle.x, particle.y), (10.0, 20.0))
        self.assertEqual((updated[0].x, updated[0].y), (11.0, 22.0))
        self.assertEqual((updated[0]._tick_prev_x, updated[0]._tick_prev_y), (10.0, 20.0))

    def test_async_snapshot_supports_slotted_particles(self):
        class SlottedParticle:
            __slots__ = ('x', 'y', 'life', '_tick_prev_x', '_tick_prev_y')

            def __init__(self):
                self.x = 4.0
                self.y = 8.0
                self.life = 1.0

            def update(self):
                self.x += 2.0

            @property
            def alive(self):
                return self.life > 0.0

        particle = SlottedParticle()

        snapshot = _snapshot_particles_for_update([particle])
        updated = _update_particles_batch(snapshot)

        self.assertIsNot(snapshot[0], particle)
        self.assertEqual((particle.x, updated[0].x), (4.0, 6.0))

    def test_async_apply_keeps_particles_created_after_snapshot(self):
        original = SimpleNamespace(x=10.0, y=20.0, life=1.0)
        extra = SimpleNamespace(x=50.0, y=60.0, life=1.0)
        updated = SimpleNamespace(x=11.0, y=22.0, _tick_prev_x=10.0, _tick_prev_y=20.0, life=1.0)
        future = Future()
        future.set_result([updated])
        reframe_calls = []
        overlay = SimpleNamespace(
            _pending_future=future,
            _pending_snapshot_ids={id(original)},
            _particles=[original, extra],
            _reframe_overlay=lambda: reframe_calls.append(True),
            hide=lambda: None,
        )

        ParticleOverlay._apply_pending_updates(overlay)

        self.assertIsNone(overlay._pending_future)
        self.assertEqual(overlay._particles, [updated, extra])
        self.assertEqual((updated._render_x, updated._render_y), (10.0, 20.0))
        self.assertEqual((extra._render_x, extra._render_y), (50.0, 60.0))
        self.assertEqual(reframe_calls, [True])

    def test_async_tick_does_not_recopy_while_update_is_pending(self):
        pending = Future()
        overlay = SimpleNamespace(
            _paused=False,
            _perf_log_enabled=False,
            _particles=[SimpleNamespace(x=1.0, y=2.0, life=1.0)] * 1200,
            _pending_future=pending,
            _apply_pending_updates=Mock(),
            _drain_particle_requests=Mock(),
        )

        with patch('lib.core.qt_particle_system._can_use_async_updates', return_value=True), patch(
            'lib.core.qt_particle_system._snapshot_particles_for_update'
        ) as snapshot:
            ParticleOverlay._on_tick(overlay, Event(EventType.TICK))

        snapshot.assert_not_called()

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
