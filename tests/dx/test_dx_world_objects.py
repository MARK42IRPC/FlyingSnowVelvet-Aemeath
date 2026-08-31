from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.offscreen import find_dx_library
from lib.core.dx_bridge.screen import DxScreenProvider
from lib.core.dx_bridge.world_object_backend import DxWorldObjectBackend
from lib.core.graphics.resources import ImageResource, RasterFrame
from lib.core.graphics.commands import TextCommand
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import Key, KeyboardInput, MouseButton
from lib.core.world_objects import WorldObjectRequest


class _PhysicsWorld:
    def __init__(self):
        self.bodies = []

    def add_body(self, body):
        self.bodies.append(body)

    def remove_body(self, body):
        if body in self.bodies:
            self.bodies.remove(body)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


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


class _Sound:
    def __init__(self):
        self.play_count = 0

    def play(self):
        self.play_count += 1


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
        self.clock = _Clock()
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
            monotonic_provider=self.clock,
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

    def test_motor_matches_qt_acceleration_release_and_jump_charges(self):
        instance_id = self.backend.create(_request("motor"))
        instance = self.backend._instances[instance_id]
        body = instance._physics_body
        instance.host.set_geometry(Rect(10, body.ground_y, 8, 6))
        instance._sync_body_to_host(active=False)

        self.backend._on_key_press(Event(EventType.KEY_PRESS, {"key": Key.LEFT}))
        self.assertEqual(body.vx, 0.0)
        instance.tick()
        self.assertEqual(body.vx, -2.0)
        self.assertFalse(body.gravity_enabled)
        instance.tick()
        self.assertEqual(body.vx, -3.0)

        self.backend._on_key_press(Event(EventType.KEY_PRESS, {"key": Key.RIGHT}))
        instance.tick()
        self.assertEqual(instance._motor_move_speed, 1.0)
        self.assertEqual(body.vx, 1.0)
        self.backend._on_key_release(Event(EventType.KEY_RELEASE, {"key": Key.LEFT}))
        instance.tick()
        self.assertEqual(body.vx, 2.0)
        self.assertFalse(instance._flipped)

        self.backend._on_key_release(Event(EventType.KEY_RELEASE, {"key": Key.RIGHT}))
        instance.tick()
        self.assertEqual(instance._motor_move_speed, 0.0)
        self.assertFalse(body.active)
        self.assertTrue(body.gravity_enabled)

        up = KeyboardInput(key=Key.UP)
        instance.handle_key_press(up)
        self.assertEqual(instance._motor_jump_charges, 1)
        self.assertEqual(body.vy, -16.0)
        instance.handle_key_press(KeyboardInput(key=Key.UP, is_auto_repeat=True))
        self.assertEqual(instance._motor_jump_charges, 1)
        instance.handle_key_release(up)
        instance.handle_key_press(up)
        self.assertEqual(instance._motor_jump_charges, 0)
        instance.handle_key_release(up)
        instance.handle_key_press(up)
        self.assertEqual(instance._motor_jump_charges, 0)

        self.clock.advance(2.0)
        instance.tick()
        self.assertEqual(instance._motor_jump_charges, 1)

    def test_motor_does_not_flip_on_wall_hit(self):
        instance_id = self.backend.create(_request("motor"))
        instance = self.backend._instances[instance_id]
        instance._flipped = False
        instance._on_physics_wall_hit(instance._physics_body, "left")
        self.assertFalse(instance._flipped)

    def test_clock_repeats_end_force_every_sixty_ticks(self):
        instance_id = self.backend.create(_request("clock", countdown_ss=1))
        instance = self.backend._instances[instance_id]
        body = instance._physics_body

        for _ in range(20):
            instance.tick()
        self.assertLess(body.vy, 0.0)
        body.vy = 0.0
        body.active = False
        for _ in range(58):
            instance.tick()
        self.assertEqual(body.vy, 0.0)
        instance.tick()
        self.assertLess(body.vy, 0.0)
        self.assertTrue(body.active)

    def test_snow_leopard_auto_flip_pauses_during_jump(self):
        with patch(
            "lib.core.dx_bridge.world_object_backend.random.uniform",
            return_value=5.0,
        ):
            instance_id = self.backend.create(_request("snow_leopard"))
            instance = self.backend._instances[instance_id]
            body = instance._physics_body
            self.assertFalse(body.active)
            self.assertEqual(body.ground_y, 20.0)

            self.clock.advance(4.9)
            instance.tick()
            self.assertFalse(instance._flipped)
            self.clock.advance(0.1)
            instance.tick()
            self.assertTrue(instance._flipped)

            body.active = True
            instance.handle_pointer_press(type("Mouse", (), {"button": MouseButton.RIGHT})())
            self.assertTrue(instance._flipped)
            body.active = False
            instance._on_physics_ground_bounce(body, stopped=True)
            self.clock.advance(5.0)
            instance.tick()
            self.assertFalse(instance._flipped)

    def test_snow_leopard_click_jump_keeps_current_facing_direction(self):
        with patch(
            "lib.core.dx_bridge.world_object_backend.random.uniform",
            return_value=1.0,
        ):
            instance_id = self.backend.create(_request("snow_leopard"))
            instance = self.backend._instances[instance_id]
            instance._flipped = False
            instance._jump_snow_leopard()
            self.assertEqual(instance._physics_body.vx, -5.0)

            instance._physics_body.active = False
            instance._flipped = True
            instance._jump_snow_leopard()
            self.assertEqual(instance._physics_body.vx, 5.0)

    def test_motor_and_snow_leopard_use_qt_anchor_offsets(self):
        motor_id = self.backend.create(_request("motor"))
        leopard_id = self.backend.create(_request("snow_leopard"))
        self.assertEqual(self.backend.get_center(motor_id), Point(14, -7))
        self.assertEqual(self.backend.get_center(leopard_id), Point(14, -7))

    def test_speaker_uses_qt_anchor_and_primary_screen_flip_rule(self):
        instance_id = self.backend.create(_request("speaker"))
        instance = self.backend._instances[instance_id]
        body = instance._physics_body

        self.assertEqual(self.backend.get_center(instance_id), Point(14, -7))
        self.assertFalse(instance._flipped)

        body.x = body.render_x = 150.0
        body.y = body.render_y = 20.0
        instance._on_physics_position_change(body)
        self.assertTrue(instance._flipped)

    def test_sofa_uses_qt_collision_particle(self):
        received = []
        center = get_event_center()
        callback = lambda event: received.append(event.data["particle_id"])
        center.subscribe(EventType.PARTICLE_REQUEST, callback)
        try:
            instance_id = self.backend.create(_request("sofa"))
            instance = self.backend._instances[instance_id]
            instance._on_physics_wall_hit(instance._physics_body, "left")
            instance._on_physics_ground_bounce(instance._physics_body, stopped=False)
        finally:
            center.unsubscribe(EventType.PARTICLE_REQUEST, callback)

        self.assertEqual(received, ["collision", "collision"])

    def test_snow_pile_clicks_match_qt_sound_and_drift_effects(self):
        received = []
        center = get_event_center()
        callback = lambda event: received.append(event.data["particle_id"])
        center.subscribe(EventType.PARTICLE_REQUEST, callback)
        sound = _Sound()
        try:
            instance_id = self.backend.create(_request("snow_pile"))
            instance = self.backend._instances[instance_id]
            instance._sounds = {"action": sound}
            click = type("Mouse", (), {
                "button": MouseButton.LEFT,
                "global_pos": Point(12, 22),
                "pos": Point(2, 2),
            })()

            instance.handle_pointer_press(click)
            instance.handle_pointer_press(click)
        finally:
            center.unsubscribe(EventType.PARTICLE_REQUEST, callback)

        self.assertTrue(instance._fading)
        self.assertEqual(sound.play_count, 2)
        self.assertEqual(received, ["snow_drift", "snow_drift"])

    def test_snow_pile_schedules_full_batch_and_cancels_on_fade(self):
        received = []
        center = get_event_center()
        callback = lambda event: received.append(event.data)
        center.subscribe(EventType.MANAGER_INTERACTION, callback)
        try:
            instance_id = self.backend.create(_request(
                "snow_pile",
                batch_interval=(10, 10),
                batch_size=(2, 2),
                batch_item_interval=(5, 5),
            ))
            instance = self.backend._instances[instance_id]
            first_call = instance._snow_pile_batch_call
            self.assertTrue(first_call.pending)
            first_call._invoke()
            second_call = instance._snow_pile_batch_call
            self.assertTrue(second_call.pending)
            second_call._invoke()

            self.assertEqual(len(received), 2)
            self.assertTrue(all(item["action"] == "spawn_leopard" for item in received))
            self.assertTrue(instance._snow_pile_batch_call.pending)
            instance.start_fadeout()
            self.assertIsNone(instance._snow_pile_batch_call)
        finally:
            center.unsubscribe(EventType.MANAGER_INTERACTION, callback)

    def test_snowball_particle_budget_matches_qt_probability_and_cap(self):
        received = []
        center = get_event_center()
        callback = lambda event: received.append(event.data["particle_id"])
        center.subscribe(EventType.PARTICLE_REQUEST, callback)
        try:
            instance_id = self.backend.create(_request("snowball"))
            instance = self.backend._instances[instance_id]
            with patch("lib.core.dx_bridge.world_object_backend.random.random", return_value=0.0):
                for _ in range(10):
                    instance._on_physics_ground_bounce(instance._physics_body, stopped=False)
            self.assertEqual(received, ["snowball_drift"] * 6)

            rejected_id = self.backend.create(_request("snowball"))
            rejected = self.backend._instances[rejected_id]
            with patch("lib.core.dx_bridge.world_object_backend.random.random", return_value=0.9):
                rejected._on_physics_ground_bounce(rejected._physics_body, stopped=False)
            self.assertEqual(received, ["snowball_drift"] * 6)
        finally:
            center.unsubscribe(EventType.PARTICLE_REQUEST, callback)


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
