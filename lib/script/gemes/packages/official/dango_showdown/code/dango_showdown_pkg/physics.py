"""Circle physics for the duel arena (pure Python, fixed-step)."""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass

from .constants import (
    BASE_RADIUS,
    LAUNCH_ANGLE_LIMIT_DEG,
    LAUNCH_SPEED_RANGE,
    WALL_JITTER_DEG,
)
from .model import SIDE_LEFT, SIDE_RIGHT

@dataclass(frozen=True, slots=True)
class Ball:
    side: str
    x: float
    y: float
    vx: float
    vy: float
    radius: float = BASE_RADIUS
    # vx/vy retain raw kinetic energy. This multiplier only scales movement;
    # unequal multipliers use a weighted elastic reflection below.
    motion_multiplier: float = 1.0

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def movement_speed(self) -> float:
        return self.speed * self.motion_multiplier

    @property
    def signature(self) -> tuple[str, float, float, float, float, float]:
        return (self.side, self.x, self.y, self.vx, self.vy, self.radius)

    def moved(self, dx: float, dy: float, vx: float | None = None, vy: float | None = None) -> "Ball":
        return Ball(
            side=self.side,
            x=self.x + dx,
            y=self.y + dy,
            vx=self.vx if vx is None else vx,
            vy=self.vy if vy is None else vy,
            radius=self.radius,
            motion_multiplier=self.motion_multiplier,
        )


@dataclass(frozen=True, slots=True)
class Contact:
    side_a: str
    side_b: str
    normal_x: float
    normal_y: float
    overlap: float
    point_x: float
    point_y: float
    relative_speed: float
    left_velocity_x: float = 0.0
    left_velocity_y: float = 0.0
    right_velocity_x: float = 0.0
    right_velocity_y: float = 0.0


@dataclass(frozen=True, slots=True)
class WallHit:
    side: str
    wall: str
    speed: float
    point_x: float
    point_y: float


@dataclass(frozen=True, slots=True)
class StepResult:
    balls: tuple[Ball, ...]
    contact: Contact | None = None
    wall_hits: tuple[WallHit, ...] = ()


def _launch(x: float, y: float, direction: int, rng: random.Random) -> Ball:
    limit = math.radians(LAUNCH_ANGLE_LIMIT_DEG)
    angle = rng.uniform(-limit, limit)
    speed = rng.uniform(*LAUNCH_SPEED_RANGE)
    side = SIDE_LEFT if direction > 0 else SIDE_RIGHT
    return Ball(side, x, y, math.cos(angle) * speed * direction, math.sin(angle) * speed)


def create_balls(rng: random.Random, speed_range: tuple[float, float] | None = None) -> tuple[Ball, Ball]:
    """Spawn the two fighters at mirrored positions with independent velocities.

    No approach force is used; the pair starts flying toward the middle only
    because the random launch angle is limited to +/-38 degrees.
    """
    del speed_range
    return _launch(0.24, 0.5, 1, rng), _launch(0.76, 0.5, -1, rng)


def step(balls: tuple[Ball, ...], dt: float) -> StepResult:
    """Plain free flight: constant speed, wall bounce and ball-ball bounce.

    There is no attraction, steering or stall recovery. A wall bounce turns
    the velocity by a tiny random angle without changing its speed; a ball
    bounce is an equal-mass elastic collision.
    """
    dt = max(0.001, min(1.0 / 30.0, float(dt)))
    walls: list[WallHit] = []
    state = []
    for ball in balls:
        x, y, vx, vy, hit = _bounce_walls(
            ball.x + ball.vx * ball.motion_multiplier * dt,
            ball.y + ball.vy * ball.motion_multiplier * dt, ball.vx, ball.vy, ball,
        )
        if hit is not None:
            walls.append(hit)
        state.append(Ball(ball.side, x, y, vx, vy, ball.radius, ball.motion_multiplier))
    contact = _bounce_pair(state, 0, 1) if len(state) == 2 else None
    return StepResult(tuple(state), contact, tuple(walls))


def _bounce_pair(state: list[Ball], index: int, other: int) -> Contact | None:
    first, second = state[index], state[other]
    dx, dy = second.x - first.x, second.y - first.y
    distance = math.hypot(dx, dy)
    if distance >= first.radius + second.radius:
        return None
    if distance <= 0.0:
        nx, ny, distance = 1.0, 0.0, 1e-9
    else:
        nx, ny = dx / distance, dy / distance
    overlap = first.radius + second.radius - distance
    vn_first = first.vx * nx + first.vy * ny
    vn_second = second.vx * nx + second.vy * ny
    m_first, m_second = first.motion_multiplier, second.motion_multiplier
    relative = m_first * vn_first - m_second * vn_second
    if relative > 0.0:
        if m_first == m_second:
            # Keep the original raw normal exchange exactly for equal speeds.
            impulse = vn_second - vn_first
            first_vx = first.vx + impulse * nx
            first_vy = first.vy + impulse * ny
            second_vx = second.vx - impulse * nx
            second_vy = second.vy - impulse * ny
        else:
            # Reflect raw normal velocity across (m_first, -m_second).
            # Effective approach becomes separation and sum(raw_v**2) stays
            # constant, even when a normal-speed body catches a slowed one.
            impulse = 2.0 * relative / (m_first * m_first + m_second * m_second)
            first_vx = first.vx - impulse * m_first * nx
            first_vy = first.vy - impulse * m_first * ny
            second_vx = second.vx + impulse * m_second * nx
            second_vy = second.vy + impulse * m_second * ny
        first = first.moved(0.0, 0.0, first_vx, first_vy)
        second = second.moved(0.0, 0.0, second_vx, second_vy)
    push = overlap * 0.5 + 1e-9
    first = _keep_inside(first.moved(-push * nx, -push * ny))
    second = _keep_inside(second.moved(push * nx, push * ny))
    state[index], state[other] = first, second
    if first.side != SIDE_LEFT and second.side == SIDE_LEFT:
        first, second = second, first
        nx, ny = -nx, -ny
    return Contact(
        side_a=first.side,
        side_b=second.side,
        normal_x=nx,
        normal_y=ny,
        overlap=overlap,
        point_x=first.x + nx * first.radius,
        point_y=first.y + ny * first.radius,
        relative_speed=abs(relative),
        left_velocity_x=first.vx,
        left_velocity_y=first.vy,
        right_velocity_x=second.vx,
        right_velocity_y=second.vy,
    )


def reset_ball_to(ball: Ball, x: float, y: float, *, vx: float | None = None, vy: float | None = None) -> Ball:
    """Teleport a ball (skill knock-back) without changing its speed."""
    return Ball(
        side=ball.side,
        x=min(max(float(x), ball.radius), 1.0 - ball.radius),
        y=min(max(float(y), ball.radius), 1.0 - ball.radius),
        vx=ball.vx if vx is None else float(vx),
        vy=ball.vy if vy is None else float(vy),
        radius=ball.radius,
        motion_multiplier=ball.motion_multiplier,
    )


def _keep_inside(ball: Ball) -> Ball:
    radius = ball.radius
    return ball.moved(
        min(max(ball.x, radius), 1.0 - radius) - ball.x,
        min(max(ball.y, radius), 1.0 - radius) - ball.y,
    )


def _bounce_walls(x: float, y: float, vx: float, vy: float, ball: Ball) -> tuple[float, float, float, float, WallHit | None]:
    radius = ball.radius
    hit: WallHit | None = None
    if x - radius < 0.0:
        x = radius
        if vx < 0.0:
            vx, vy = _turn_velocity(-vx, vy, ball.side, "left", x, y)
            hit = WallHit(ball.side, "left", math.hypot(vx, vy), radius, y)
    elif x + radius > 1.0:
        x = 1.0 - radius
        if vx > 0.0:
            vx, vy = _turn_velocity(-vx, vy, ball.side, "right", x, y)
            hit = WallHit(ball.side, "right", math.hypot(vx, vy), 1.0 - radius, y)
    if y - radius < 0.0:
        y = radius
        if vy < 0.0:
            vx, vy = _turn_velocity(vx, -vy, ball.side, "top", x, y)
            hit = WallHit(ball.side, "top", math.hypot(vx, vy), x, radius)
    elif y + radius > 1.0:
        y = 1.0 - radius
        if vy > 0.0:
            vx, vy = _turn_velocity(vx, -vy, ball.side, "bottom", x, y)
            hit = WallHit(ball.side, "bottom", math.hypot(vx, vy), x, 1.0 - radius)
    # A grazing turn (or a second turn at a corner) must still point into
    # the arena; otherwise one wall touch can repeatedly trigger skills.
    if x <= radius:
        vx = abs(vx)
    elif x >= 1.0 - radius:
        vx = -abs(vx)
    if y <= radius:
        vy = abs(vy)
    elif y >= 1.0 - radius:
        vy = -abs(vy)
    return x, y, vx, vy, hit


def _turn_velocity(vx: float, vy: float, side: str, wall: str, x: float, y: float) -> tuple[float, float]:
    """Rotate the wall-reflected velocity; the speed is preserved exactly."""
    angle = math.atan2(vy, vx)
    # Current flight state alone makes replay independent of other matches
    # and of how fixed steps are grouped into rendered frames.
    seed_text = f"{side}:{wall}:{x:.12f}:{y:.12f}:{vx:.12f}:{vy:.12f}"
    digest = zlib.crc32(seed_text.encode("ascii"))
    turn = ((digest / 0xFFFFFFFF) * 2.0 - 1.0) * math.radians(WALL_JITTER_DEG)
    speed = math.hypot(vx, vy)
    return speed * math.cos(angle + turn), speed * math.sin(angle + turn)


__all__ = [
    "Ball",
    "Contact",
    "StepResult",
    "WallHit",
    "create_balls",
    "reset_ball_to",
    "step",
]
