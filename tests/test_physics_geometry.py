from lib.core.graphics.types import Rect
from lib.core.physics import PhysicsBody, PhysicsWorld, _step_physics_batch


def test_physics_batch_is_independent_of_screen_backend():
    body = PhysicsBody(0, 0, 100, 10, 10)
    body.active = True
    updates = _step_physics_batch(
        [{
            "body": body,
            "x": body.x,
            "y": body.y,
            "vx": 1.0,
            "vy": 0.0,
            "ground_y": body.ground_y,
            "width": body.width,
            "height": body.height,
            "max_bounces": body.max_bounces,
            "bounce_count": body.bounce_count,
            "gravity_enabled": False,
            "active": True,
            "bounce_vx_retain": None,
            "state_version": body.state_version,
        }],
        gravity=0.55,
        bounce_vy_retain=0.45,
        bounce_vx_retain=0.8,
        min_bounce_vy=1.5,
        min_velocity=0.1,
        screen_left=0,
        screen_right=100,
        screen_top=0,
        screen_bottom=100,
    )

    assert updates[0]["x"] > 0
    assert updates[0]["body"] is body


def test_physics_world_accepts_core_screen_bounds_provider():
    world = PhysicsWorld(lambda: Rect(-100, -50, 800, 600))
    try:
        world._refresh_screen_bounds()
        assert (world._screen_left, world._screen_top) == (-100, -50)
        assert (world._screen_right, world._screen_bottom) == (700, 550)
    finally:
        world.cleanup()
