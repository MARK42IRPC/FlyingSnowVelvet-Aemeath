"""Product physics parameters resolved once for every rendering backend.

Both implementations of the world-object protocol need the same numbers: the Qt
widgets in ``lib/script/ui/world_objects`` and the DirectX simulation in
``lib/core/render/backends/dx/world_object_backend``. Before this module existed each side
read ``config.config`` (``PHYSICS`` / ``MORTOR`` / ``SNOWBALL`` / ``SNOW_LEOPARD``
/ ``BEHAVIOR``) on its own, so a single missing key in either copy could give the
two backends different motion, fade speed or jump power for the same object.

The rule this module enforces: **product numbers are resolved here, once.** A
backend may still choose ``0.10`` for its own drag-trail window or how many ticks
between fade steps -- those are technical, not product, decisions.

``DEFAULT_*`` constants are the old inline fallbacks, kept verbatim so behaviour
is unchanged for any key a user has not overridden. Two of them are worth
explaining:

* ``DEFAULT_JUMP_VY`` is the nested fallback the motor uses for its jump:
  ``MORTOR.jump_vy`` else ``PHYSICS.snow_leopard_jump_vy`` else -13.0. The
  leopard and the clock's end-up force read ``PHYSICS.snow_leopard_jump_vy``
  directly, which is a different key from ``MORTOR.jump_vy`` and may hold a
  different value.
* ``DEFAULT_GROUND_Y_PCT`` is a fraction of screen height, not a pixel count, so
  it is deliberately not routed through ``scale_px``.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.config import BEHAVIOR, MORTOR, PHYSICS, SNOWBALL, SNOW_LEOPARD

DEFAULT_GROUND_Y_PCT = 0.90
DEFAULT_MAX_THROW_VX = 25.0
DEFAULT_MAX_THROW_VY = 25.0
DEFAULT_DRAG_THRESHOLD = 5
DEFAULT_FADE_STEP = 0.05
DEFAULT_FADE_INTERVAL_MS = 50
DEFAULT_MAX_BOUNCES = 5
DEFAULT_DRAG_TRAIL_WINDOW_SEC = 0.10
DEFAULT_RELEASE_SAMPLE_MIN_DT_SEC = 1.0 / 60.0
DEFAULT_DOUBLE_CLICK_TICKS = 3
DEFAULT_MOTOR_BASE_SPEED = 2.0
DEFAULT_MOTOR_ACCEL_PER_TICK = 1.0
DEFAULT_MOTOR_DECEL_PER_TICK = 2.0
DEFAULT_MOTOR_MAX_SPEED = 10.0
DEFAULT_JUMP_VY = -13.0
DEFAULT_MOTOR_JUMP_COOLDOWN_SEC = 2.0
DEFAULT_MOTOR_JUMP_MAX_CHARGES = 2
DEFAULT_SNOW_LEOPARD_JUMP_VX = 5.0
DEFAULT_SNOW_LEOPARD_JUMP_POWER_MIN = 0.8
DEFAULT_SNOW_LEOPARD_JUMP_POWER_MAX = 1.2
DEFAULT_SNOW_LEOPARD_ANCHOR_OFFSET_Y = -30.0
DEFAULT_FLIP_INTERVAL_MIN_MS = 5000.0
DEFAULT_FLIP_INTERVAL_MAX_MS = 8000.0
DEFAULT_SNOWBALL_LIFETIME_MIN_SEC = 10.0
DEFAULT_SNOWBALL_LIFETIME_MAX_SEC = 15.0
DEFAULT_SNOWBALL_GROUND_FRICTION = 0.96

#: Fade ticks are counted on a fixed 50ms simulation step.
FADE_TICK_MS = 50
#: The clock's countdown-end push is this many times the jump impulse.
CLOCK_UP_FORCE_MULTIPLIER = 2.0


def _float(value: object, fallback: float) -> float:
    """Coerce a config value, falling back on anything unusable.

    ``bool`` is rejected on purpose: it is an ``int`` subclass, so ``True`` would
    otherwise silently become ``1.0``.
    """
    if isinstance(value, bool):
        return fallback
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True, slots=True)
class WorldObjectPhysics:
    """Product parameters for the interactive world objects.

    Fields are grouped the way the config file groups them: shared physics first,
    then motor-only, then the remaining per-type values.
    """

    # -- shared PHYSICS ----------------------------------------------------
    ground_y_pct: float
    max_throw_vx: float
    max_throw_vy: float
    drag_threshold: int
    fade_step: float
    fade_interval_ms: int
    max_bounces: int

    # -- BEHAVIOR ----------------------------------------------------------
    double_click_ticks: int

    # -- MORTOR ------------------------------------------------------------
    motor_move_speed_px_per_frame: float
    motor_move_accel_per_tick: float
    motor_move_decel_per_tick: float
    motor_move_speed_max: float
    motor_jump_vy: float
    motor_jump_cooldown_sec: float
    motor_jump_max_charges: int

    # -- SNOW_LEOPARD / snowball ------------------------------------------
    snow_leopard_jump_vx: float
    snow_leopard_jump_vy: float
    snow_leopard_jump_power_min: float
    snow_leopard_jump_power_max: float
    snow_leopard_anchor_offset_y: float
    flip_interval_min_ms: float
    flip_interval_max_ms: float
    snowball_lifetime_min_sec: float
    snowball_lifetime_max_sec: float
    snowball_ground_friction: float

    @property
    def fade_tick_stride(self) -> int:
        """Whole simulation ticks per fade step; never below one."""
        return max(1, int(round(self.fade_interval_ms / FADE_TICK_MS)))

    @property
    def clock_up_force_vy(self) -> float:
        """Vertical impulse the clock applies when a countdown ends."""
        return self.snow_leopard_jump_vy * CLOCK_UP_FORCE_MULTIPLIER

    @property
    def snow_leopard_flip_interval_seconds(self) -> tuple[float, float]:
        """Flip interval in seconds; the config stores milliseconds."""
        return (
            self.flip_interval_min_ms / 1000.0,
            self.flip_interval_max_ms / 1000.0,
        )

    def clamp_throw(self, vx: float, vy: float) -> tuple[float, float]:
        """Clamp a released drag velocity to the configured throw limits."""
        return (
            max(-self.max_throw_vx, min(self.max_throw_vx, vx)),
            max(-self.max_throw_vy, min(self.max_throw_vy, vy)),
        )


def resolve_world_object_physics() -> WorldObjectPhysics:
    """Read the product physics configuration once and normalise it.

    Every value is coerced here so a malformed user override produces the same
    documented fallback in both backends instead of a ``TypeError`` in whichever
    one happens to load first.
    """
    legacy_jump_vy = _float(
        PHYSICS.get("snow_leopard_jump_vy"), DEFAULT_JUMP_VY
    )
    return WorldObjectPhysics(
        ground_y_pct=_float(PHYSICS.get("ground_y_pct"), DEFAULT_GROUND_Y_PCT),
        max_throw_vx=_float(PHYSICS.get("max_throw_vx"), DEFAULT_MAX_THROW_VX),
        max_throw_vy=_float(PHYSICS.get("max_throw_vy"), DEFAULT_MAX_THROW_VY),
        drag_threshold=_int(PHYSICS.get("drag_threshold"), DEFAULT_DRAG_THRESHOLD),
        fade_step=_float(PHYSICS.get("fade_step"), DEFAULT_FADE_STEP),
        fade_interval_ms=_int(
            PHYSICS.get("fade_interval_ms"), DEFAULT_FADE_INTERVAL_MS
        ),
        max_bounces=_int(PHYSICS.get("max_bounces"), DEFAULT_MAX_BOUNCES),
        double_click_ticks=_int(
            BEHAVIOR.get("double_click_ticks"), DEFAULT_DOUBLE_CLICK_TICKS
        ),
        motor_move_speed_px_per_frame=_float(
            MORTOR.get("move_speed_px_per_frame"), DEFAULT_MOTOR_BASE_SPEED
        ),
        motor_move_accel_per_tick=_float(
            MORTOR.get("move_accel_per_tick"), DEFAULT_MOTOR_ACCEL_PER_TICK
        ),
        motor_move_decel_per_tick=_float(
            MORTOR.get("move_decel_per_tick"), DEFAULT_MOTOR_DECEL_PER_TICK
        ),
        motor_move_speed_max=_float(
            MORTOR.get("move_speed_max"), DEFAULT_MOTOR_MAX_SPEED
        ),
        motor_jump_vy=_float(MORTOR.get("jump_vy"), legacy_jump_vy),
        snow_leopard_jump_vy=legacy_jump_vy,
        motor_jump_cooldown_sec=_float(
            MORTOR.get("jump_cooldown_sec"), DEFAULT_MOTOR_JUMP_COOLDOWN_SEC
        ),
        motor_jump_max_charges=max(
            1,
            _int(
                MORTOR.get("jump_max_charges"), DEFAULT_MOTOR_JUMP_MAX_CHARGES
            ),
        ),
        snow_leopard_jump_vx=_float(
            PHYSICS.get("snow_leopard_jump_vx"), DEFAULT_SNOW_LEOPARD_JUMP_VX
        ),
        snow_leopard_jump_power_min=_float(
            SNOW_LEOPARD.get("jump_power_min"), DEFAULT_SNOW_LEOPARD_JUMP_POWER_MIN
        ),
        snow_leopard_jump_power_max=_float(
            SNOW_LEOPARD.get("jump_power_max"), DEFAULT_SNOW_LEOPARD_JUMP_POWER_MAX
        ),
        snow_leopard_anchor_offset_y=_float(
            SNOW_LEOPARD.get("anchor_offset_y"), DEFAULT_SNOW_LEOPARD_ANCHOR_OFFSET_Y
        ),
        flip_interval_min_ms=_float(
            PHYSICS.get("flip_interval_min"), DEFAULT_FLIP_INTERVAL_MIN_MS
        ),
        flip_interval_max_ms=_float(
            PHYSICS.get("flip_interval_max"), DEFAULT_FLIP_INTERVAL_MAX_MS
        ),
        snowball_lifetime_min_sec=_float(
            SNOWBALL.get("lifetime_min"), DEFAULT_SNOWBALL_LIFETIME_MIN_SEC
        ),
        snowball_lifetime_max_sec=_float(
            SNOWBALL.get("lifetime_max"), DEFAULT_SNOWBALL_LIFETIME_MAX_SEC
        ),
        snowball_ground_friction=_float(
            SNOWBALL.get("ground_friction"), DEFAULT_SNOWBALL_GROUND_FRICTION
        ),
    )


__all__ = [
    "CLOCK_UP_FORCE_MULTIPLIER",
    "DEFAULT_DRAG_THRESHOLD",
    "DEFAULT_DRAG_TRAIL_WINDOW_SEC",
    "DEFAULT_DOUBLE_CLICK_TICKS",
    "DEFAULT_FADE_INTERVAL_MS",
    "DEFAULT_FADE_STEP",
    "DEFAULT_FLIP_INTERVAL_MAX_MS",
    "DEFAULT_FLIP_INTERVAL_MIN_MS",
    "DEFAULT_GROUND_Y_PCT",
    "DEFAULT_JUMP_VY",
    "DEFAULT_MAX_BOUNCES",
    "DEFAULT_MAX_THROW_VX",
    "DEFAULT_MAX_THROW_VY",
    "DEFAULT_RELEASE_SAMPLE_MIN_DT_SEC",
    "DEFAULT_SNOWBALL_GROUND_FRICTION",
    "DEFAULT_SNOWBALL_LIFETIME_MAX_SEC",
    "DEFAULT_SNOWBALL_LIFETIME_MIN_SEC",
    "DEFAULT_SNOW_LEOPARD_ANCHOR_OFFSET_Y",
    "DEFAULT_SNOW_LEOPARD_JUMP_POWER_MAX",
    "DEFAULT_SNOW_LEOPARD_JUMP_POWER_MIN",
    "DEFAULT_SNOW_LEOPARD_JUMP_VX",
    "FADE_TICK_MS",
    "WorldObjectPhysics",
    "resolve_world_object_physics",
]
