from __future__ import annotations

import os
import unittest

from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.offscreen import find_dx_library
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.dx_bridge.world_object_backend import DxWorldObjectBackend
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.commands import TextCommand
from lib.core.graphics.types import Point, Rect
from lib.core.world_objects import WorldObjectRequest


class _PhysicsWorld:
    def __init__(self):
        self.bodies = []

    def add_body(self, body):
        self.bodies.append(body)

    def remove_body(self, body):
        if body in self.bodies:
            self.bodies.remove(body)


class _Host:
    _next_identity = 1

    def __init__(self, width, height, *, x=0, y=0, callbacks=None, **kwargs):
        self.identity = _Host._next_identity
        _Host._next_identity += 1
        self.callbacks = callbacks
        self._geometry = Rect(x, y, width, height)
        self._alive = True
        self._visible = False
        self._clickthrough = bool(kwargs.get("clickthrough", False))
        self.repaint_count = 0

    @property
    def native_handle(self):
        return self.identity

    def is_alive(self):
        return self._alive

    def is_visible(self):
        return self._alive and self._visible

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def get_geometry(self):
        return self._geometry

    def set_geometry(self, geometry):
        self._geometry = geometry

    def set_clickthrough(self, enabled):
        self._clickthrough = bool(enabled)

    def request_repaint(self, viewport=None):
        self.repaint_count += 1

    def poll_events(self):
        return ()

    def raise_window(self):
        return None

    def stack_window(self, insert_after):
        return self.native_handle

    def cleanup(self):
        self._alive = False
        self._visible = False


def _resource(frame_count=1):
    frames = tuple(
        RasterFrame(2, 2, bytes((20 + index, 30, 40, 255)) * 4)
        for index in range(frame_count)
    )
    return ImageResource("world:test", frames)


def _request(object_type="snowball", frame_count=1, **options):
    return WorldObjectRequest(
        object_type,
        _resource(frame_count),
        Point(10, 20),
        (8, 6),
        tuple(options.items()),
    )


class DxWorldObjectBackendTests(unittest.TestCase):
    def setUp(self):
        self.context = DxLoopContext()
        self.physics = _PhysicsWorld()
        self.provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(0, 0, 200, 120),
        )
        self.backend = DxWorldObjectBackend(
            self.context,
            screen_provider=self.provider,
            physics_world=self.physics,
            window_host_factory=_Host,
            cursor_position_provider=lambda: Point(40, 50),
        )

    def tearDown(self):
        self.backend.cleanup()
        self.backend.cleanup()

    def test_snowball_geometry_motion_delta_and_fade_cleanup(self):
        instance_id = self.backend.create(_request())
        self.assertEqual(self.backend.get_geometry(instance_id), Rect(10, 20, 8, 6))
        self.assertEqual(self.backend.get_center(instance_id), Point(14, 23))
        self.assertEqual(self.backend.get_motion(instance_id).radius, 4.0)
        self.assertEqual(len(self.physics.bodies), 1)

        self.backend.apply_motion_delta(
            instance_id,
            position=Point(3, -2),
            velocity=Point(1, 2),
            wake=True,
        )
        motion = self.backend.get_motion(instance_id)
        self.assertEqual(motion.position, Point(13, 18))
        self.assertEqual(motion.velocity, Point(1, 2))

        self.backend.start_fadeout(instance_id)
        for _ in range(30):
            self.backend._on_tick(None)
        self.assertFalse(self.backend.get_state(instance_id).alive)
        self.assertEqual(self.physics.bodies, [])

    def test_animation_clickthrough_and_close_are_idempotent(self):
        instance_id = self.backend.create(_request("snow_leopard", frame_count=2))
        instance = self.backend._instances[instance_id]
        initial_repaints = instance.host.repaint_count
        self.backend._on_gif_frame(None)
        self.assertEqual(instance._frame_index, 1)
        self.assertGreater(instance.host.repaint_count, initial_repaints)

        self.backend._on_clickthrough_toggle(type("Event", (), {"data": {"enabled": True}})())
        self.assertTrue(instance.host._clickthrough)
        self.backend.close(instance_id)
        self.backend.close(instance_id)
        self.assertFalse(self.backend.get_state(instance_id).alive)

    def test_clock_uses_shared_countdown_visual(self):
        instance_id = self.backend.create(_request("clock", countdown_ss=1))
        instance = self.backend._instances[instance_id]
        text = next(
            command
            for command in instance.prepare_render().commands
            if isinstance(command, TextCommand)
        )
        self.assertEqual(text.text, "01:00")

        for _ in range(20):
            instance.tick()
        text = next(
            command
            for command in instance.prepare_render().commands
            if isinstance(command, TextCommand)
        )
        self.assertEqual(text.text, "00:00")


@unittest.skipUnless(
    os.name == "nt" and find_dx_library() is not None,
    "DX world-object integration requires Windows and a built DX DLL",
)
class DxWorldObjectIntegrationTests(unittest.TestCase):
    def test_real_window_renders_and_cleans_world_object(self):
        context = DxLoopContext()
        physics = _PhysicsWorld()
        provider = DxScreenProvider(
            monitor_loader=lambda: (),
            fallback=Rect(0, 0, 200, 120),
        )
        backend = DxWorldObjectBackend(
            context,
            screen_provider=provider,
            physics_world=physics,
            warp=True,
        )
        try:
            instance_id = backend.create(_request("motor"))
            instance = backend._instances[instance_id]
            context.run_once()
            self.assertTrue(instance.host.is_visible())
            self.assertEqual(len(instance.host.readback_rgba()), 8 * 6 * 4)
            backend.close(instance_id)
            self.assertFalse(instance.host.is_alive())
        finally:
            backend.cleanup()


if __name__ == "__main__":
    unittest.main()
