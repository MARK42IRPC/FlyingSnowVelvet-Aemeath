import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtCore import QRect, QRectF
from PyQt5.QtGui import QRegion

from lib.core.event.center import Event, EventType
from lib.core.qt_particle_system import (
    ParticleOverlay,
    _build_particle_grid,
    _particle_bounds,
    _particles_for_region,
    _prepare_particles_for_inplace_update,
    _region_for_tiles,
    _snapshot_particles_for_update,
    _tile_keys_for_bounds,
    _update_particles_batch,
)


class ParticleBoundsTests(unittest.TestCase):
    def test_tick_interpolation_starts_from_logical_position(self):
        particle = SimpleNamespace(
            x=10.0,
            y=20.0,
            _render_x=10.5,
            _render_y=20.25,
        )

        _prepare_particles_for_inplace_update([particle])

        self.assertEqual((particle._tick_prev_x, particle._tick_prev_y), (10.0, 20.0))

    def test_tile_keys_cover_positive_and_negative_coordinates(self):
        self.assertEqual(
            _tile_keys_for_bounds(QRectF(-4.0, -4.0, 136.0, 136.0)),
            {(-1, -1), (0, -1), (1, -1), (-1, 0), (0, 0), (1, 0),
             (-1, 1), (0, 1), (1, 1)},
        )

    def test_grid_indexes_particle_in_each_intersected_tile(self):
        particle = SimpleNamespace(x=128.0, y=64.0, size=20.0, life=1.0)

        grid, occupied = _build_particle_grid([particle])

        self.assertEqual(occupied, {(0, 0), (1, 0)})
        self.assertIs(grid[(0, 0)][0], particle)
        self.assertIs(grid[(1, 0)][0], particle)

    def test_region_query_deduplicates_particle_spanning_tiles(self):
        particle = SimpleNamespace(x=128.0, y=64.0, size=20.0, life=1.0)
        grid, occupied = _build_particle_grid([particle])

        particles = _particles_for_region(grid, _region_for_tiles(occupied))

        self.assertEqual(particles, [particle])

    def test_region_query_skips_distant_occupied_tiles(self):
        near = SimpleNamespace(x=20.0, y=20.0, size=8.0, life=1.0)
        far = SimpleNamespace(x=4000.0, y=20.0, size=8.0, life=1.0)
        grid, _occupied = _build_particle_grid([near, far])

        particles = _particles_for_region(grid, QRegion(QRect(0, 0, 128, 128)))

        self.assertEqual(particles, [near])

    def test_spatial_refresh_invalidates_old_and_new_tiles_without_geometry_change(self):
        particle = SimpleNamespace(x=20.0, y=20.0, size=8.0, life=1.0)
        updates = []
        overlay = SimpleNamespace(
            _particles=[particle],
            _spatial_grid={},
            _occupied_tiles={(0, 0)},
            rect=lambda: QRect(0, 0, 512, 256),
            update=lambda region: updates.append(region),
        )
        particle.x = 300.0

        ParticleOverlay._refresh_spatial_grid(overlay)

        self.assertEqual(overlay._occupied_tiles, {(2, 0)})
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0].contains(QRect(0, 0, 128, 128)))
        self.assertTrue(updates[0].contains(QRect(256, 0, 128, 128)))

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
        refresh_calls = []
        overlay = SimpleNamespace(
            _pending_future=future,
            _pending_snapshot_ids={id(original)},
            _particles=[original, extra],
            _refresh_spatial_grid=lambda: refresh_calls.append(True),
            hide=lambda: None,
        )

        ParticleOverlay._apply_pending_updates(overlay)

        self.assertIsNone(overlay._pending_future)
        self.assertEqual(overlay._particles, [updated, extra])
        self.assertEqual((updated._render_x, updated._render_y), (10.0, 20.0))
        self.assertEqual((extra._render_x, extra._render_y), (50.0, 60.0))
        self.assertEqual(refresh_calls, [])

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
