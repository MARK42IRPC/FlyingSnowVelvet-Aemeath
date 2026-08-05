import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QImage

from lib.core.graphics.types import Point, Rect
from lib.core.layer import Layer
from lib.core.pet_window import PetWindow
from lib.core.qt_bridge.window import (
    coerce_qpoint,
    move_widget,
    render_draw_core,
    set_pet_window_clickthrough,
    to_qpoint,
)
from lib.core.qt_bridge.window_host import QtLayerWindowHost
from lib.core.qt_bridge.window_setup import finalize_pet_window_startup


class _MoveProbe:
    def __init__(self):
        self.position = None

    def move(self, position):
        self.position = position


class _WindowProbe:
    def __init__(self):
        self.actions = []

    def hide(self):
        self.actions.append(("hide",))

    def show(self):
        self.actions.append(("show",))

    def setAttribute(self, attribute, enabled):
        self.actions.append(("attribute", attribute, enabled))

    def setWindowFlags(self, flags):
        self.actions.append(("flags", flags))


class _LayerWindowProbe:
    def __init__(self, handle=77):
        self.handle = handle
        self.visible = True
        self.raised = 0

    def isVisible(self):
        return self.visible

    def winId(self):
        return self.handle

    def raise_(self):
        self.raised += 1


class _DrawCoreProbe:
    def __init__(self):
        self.calls = []

    def render(self, painter, viewport):
        self.calls.append((painter.isActive(), viewport))


class _MoveParticleProbe:
    def __init__(self):
        self._move_particle_last_pos = QPoint(10, 20)
        self._move_particle_enabled = True
        self._move_particle_distance_accum = 0.0
        self._move_particle_step_px = 30.0
        self.spawned = []

    def get_core_geometry(self):
        return Rect(40, 20, 100, 80)

    def spawn_particles(self, *args, **kwargs):
        self.spawned.append((args, kwargs))


class QtWindowBridgeTests(unittest.TestCase):
    def test_layer_window_host_confines_qwidget_and_win32_operations(self):
        probe = _LayerWindowProbe()
        calls = []

        def set_window_pos(*args):
            calls.append(args)
            return True

        host = QtLayerWindowHost(probe, set_window_pos_api=set_window_pos)

        self.assertEqual(host.identity, id(probe))
        self.assertTrue(host.is_alive())
        self.assertTrue(host.is_visible())
        self.assertEqual(host.stack_window(None), 77)
        self.assertEqual(host.stack_window(88), 77)
        self.assertEqual(calls[0][0:2], (77, -1))
        self.assertEqual(calls[1][0:2], (77, 88))
        self.assertEqual(calls[0][-1], 0x0213)

        host.raise_window()
        self.assertEqual(probe.raised, 1)

    def test_layer_window_host_reports_failed_native_stacking(self):
        probe = _LayerWindowProbe()
        host = QtLayerWindowHost(
            probe,
            set_window_pos_api=lambda *_: False,
        )

        self.assertIsNone(host.stack_window(None))

    def test_point_conversion_and_widget_move_are_confined_to_bridge(self):
        probe = _MoveProbe()

        move_widget(probe, Point(12.4, 33.6))

        self.assertEqual(probe.position, QPoint(12, 34))
        self.assertEqual(to_qpoint((5, 7)), QPoint(5, 7))
        self.assertEqual(coerce_qpoint(Point(8.2, 9.8)), QPoint(8, 10))
        self.assertIsNone(coerce_qpoint(None))

    def test_clickthrough_flags_preserve_existing_window_behavior(self):
        probe = _WindowProbe()

        set_pet_window_clickthrough(probe, True)

        self.assertEqual(probe.actions[0], ("hide",))
        self.assertEqual(
            probe.actions[1],
            ("attribute", Qt.WA_TransparentForMouseEvents, True),
        )
        self.assertEqual(probe.actions[-1], ("show",))

    def test_draw_core_is_rendered_with_an_active_qpainter(self):
        image = QImage(8, 6, QImage.Format_ARGB32_Premultiplied)
        draw_core = _DrawCoreProbe()

        render_draw_core(image, draw_core)

        self.assertEqual(len(draw_core.calls), 1)
        active, viewport = draw_core.calls[0]
        self.assertTrue(active)
        self.assertEqual(viewport, Rect(0, 0, 8, 6))

    def test_startup_stores_backend_neutral_move_particle_position(self):
        owner = Mock()
        owner.get_core_position.return_value = Point(12, 34)
        layer_manager = Mock()

        with patch(
            "lib.core.qt_bridge.window_setup.get_layer_manager",
            return_value=layer_manager,
        ):
            finalize_pet_window_startup(owner)

        self.assertEqual(owner._move_particle_last_pos, Point(12, 34))
        layer_manager.register.assert_called_once_with(
            owner,
            Layer.MAIN_PET,
            name="PetWindow",
        )

    def test_move_particle_tracking_normalizes_legacy_qpoint_cache(self):
        probe = _MoveParticleProbe()

        PetWindow._track_move_particles(probe, Point(40, 20))

        self.assertEqual(probe._move_particle_last_pos, Point(40, 20))
        self.assertEqual(probe._move_particle_distance_accum, 0.0)
        self.assertEqual(
            probe.spawned,
            [((90, 60), {"particle_id": "flicker_data", "area_type": "point"})],
        )


if __name__ == "__main__":
    unittest.main()
