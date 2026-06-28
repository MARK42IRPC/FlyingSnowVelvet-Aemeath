"""物理系统 - 统一管理物理体的运动模拟（重力、弹力、屏幕边界碰撞）

设计原则：
  - PhysicsBody : 轻量数据容器，存储运动状态 + 三类回调
  - PhysicsWorld: 全局单例，订阅 FRAME 事件，每帧更新所有活跃物理体
  - 调用方只需注册回调，不必关心物理步进细节（KISS）

坐标系说明：
  - 与 Qt 一致：x 向右为正，y 向下为正
  - ground_y：物理体的"地面" Y 坐标（窗口左上角 Y），各物理体独立
  - 屏幕边界使用虚拟桌面（覆盖全部屏幕）
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PyQt5.QtWidgets import QApplication

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from config.config import PHYSICS
from lib.core.logger import get_logger
from lib.core.screen_utils import get_virtual_screen_geometry

_logger = get_logger(__name__)


def _log(msg: str) -> None:
    _logger.debug("[PhysicsWorld] %s", msg)


def _step_physics_batch(
    snapshot: list[dict],
    *,
    gravity: float,
    bounce_vy_retain: float,
    bounce_vx_retain: float,
    min_bounce_vy: float,
    min_velocity: float,
    screen_left: int,
    screen_right: int,
    screen_top: int,
    screen_bottom: int,
    substeps: int = 1,
) -> list[dict]:
    """后台批量步进物理体，返回主线程可应用的纯数据结果。"""
    updates: list[dict] = []
    substeps = max(1, int(substeps))

    for item in snapshot:
        x = float(item["x"])
        y = float(item["y"])
        vx = float(item["vx"])
        vy = float(item["vy"])
        ground_y = float(item["ground_y"])
        width = int(item["width"])
        height = int(item["height"])
        max_bounces = int(item["max_bounces"])
        bounce_count = int(item["bounce_count"])
        gravity_enabled = bool(item["gravity_enabled"])
        active = bool(item["active"])
        body_bounce_vx_retain = item["bounce_vx_retain"]

        if not active:
            continue

        wall_hit_side = None
        ground_stopped = None

        left_limit = screen_left
        right_limit = screen_right - width
        top_limit = screen_top
        bottom_limit = screen_bottom - height

        for _ in range(substeps):
            if not active:
                break

            if gravity_enabled:
                vy += gravity

            x += vx
            y += vy

            if x <= left_limit:
                x = float(left_limit)
                vx = abs(vx)
                wall_hit_side = "left"
            elif x >= right_limit:
                x = float(right_limit)
                vx = -abs(vx)
                wall_hit_side = "right"

            if gravity_enabled and y >= ground_y:
                y = ground_y
                vy = -abs(vy) * bounce_vy_retain
                retain = (
                    float(body_bounce_vx_retain)
                    if body_bounce_vx_retain is not None
                    else bounce_vx_retain
                )
                vx *= retain
                bounce_count += 1
                stopped = abs(vy) < min_bounce_vy or bounce_count >= max_bounces
                if stopped:
                    vy = 0.0
                    vx = 0.0
                    active = False
                ground_stopped = stopped

            if not gravity_enabled:
                if y <= top_limit:
                    y = float(top_limit)
                    vy = abs(vy)
                    wall_hit_side = "top"
                elif y >= bottom_limit:
                    y = float(bottom_limit)
                    vy = -abs(vy)
                    wall_hit_side = "bottom"

            if active:
                speed = math.sqrt(vx * vx + vy * vy)
                speed_factor = min(speed / 30.0, 1.0)
                dynamic_resistance = 0.995 - (speed_factor * 0.035)
                vx *= dynamic_resistance
                vy *= dynamic_resistance

                near_ground = (not gravity_enabled) or (y >= ground_y - 1.0)
                if near_ground and speed < min_velocity:
                    vx = 0.0
                    vy = 0.0
                    active = False

        updates.append({
            "body": item["body"],
            "x": x,
            "y": y,
            "vx": vx,
            "vy": vy,
            "active": active,
            "bounce_count": bounce_count,
            "wall_hit_side": wall_hit_side,
            "ground_stopped": ground_stopped,
        })

    return updates


# ══════════════════════════════════════════════════════════════════════
# 物理体
# ══════════════════════════════════════════════════════════════════════

class PhysicsBody:
    """
    单个物理体。

    存储位置、速度、地面坐标等运动状态，
    以及位置变化、边界碰撞、地面反弹三类回调。
    调用方通过注册回调来响应物理事件，无需继承。
    """

    def __init__(
        self,
        x: float,
        y: float,
        ground_y: float,
        width: int,
        height: int,
        max_bounces: int = 3,
    ) -> None:
        """
        Args:
            x, y        : 初始位置（窗口左上角像素坐标）
            ground_y    : 地面 Y 坐标（各物理体独立，通常为生成时的 y）
            width       : 物理体宽度（像素），用于计算右边界碰撞
            height      : 物理体高度（像素）
            max_bounces : 每次激活允许的最大地面弹跳次数
        """
        self.x: float = x
        self.y: float = y
        self.vx: float = 0.0
        self.vy: float = 0.0
        self.prev_x: float = x
        self.prev_y: float = y
        self.render_x: float = x
        self.render_y: float = y

        self.ground_y: float = ground_y
        self.width: int = width
        self.height: int = height
        self.max_bounces: int = max_bounces

        # True 时 PhysicsWorld 每帧执行步进；False 时跳过
        self.active: bool = False
        # 当前弹跳序列中已触地次数
        self.bounce_count: int = 0
        # 重力开关（True = 受重力影响，False = 不受重力）
        self.gravity_enabled: bool = True

        # ── per-body 物理覆盖（None = 使用 PhysicsWorld 世界常数）───
        # 触地水平速度保留比例（地面摩擦）；None 时沿用 BOUNCE_VX_RETAIN
        self.bounce_vx_retain: Optional[float] = None

        # ── 回调（调用方注册） ────────────────────────────────────
        # 每帧位置变化后触发（仅 active=True 期间）
        self.on_position_change: Optional[Callable[[PhysicsBody], None]] = None
        # 碰到左/右屏幕边界时触发；side='left' 或 'right'
        self.on_wall_hit: Optional[Callable[[PhysicsBody, str], None]] = None
        # 触地时触发；stopped=True 表示本次弹跳序列已结束（active 已置 False）
        self.on_ground_bounce: Optional[Callable[[PhysicsBody, bool], None]] = None


# ══════════════════════════════════════════════════════════════════════
# 物理世界
# ══════════════════════════════════════════════════════════════════════

class PhysicsWorld:
    """
    物理世界（全局单例）。

    每 FRAME 事件（60fps）对所有 active=True 的物理体执行一步：

      1. 施加重力：vy += GRAVITY
      2. 移动：    x += vx,  y += vy
      3. 左/右屏幕边界碰撞：
           水平速度取反，触发 on_wall_hit 回调
      4. 地面碰撞：
           vy 按 BOUNCE_VY_RETAIN 衰减并反弹（弹性），
           vx 按 BOUNCE_VX_RETAIN 轻微衰减（地面摩擦）；
           速度低于 MIN_BOUNCE_VY 或达到 max_bounces 时停止，
           触发 on_ground_bounce 回调
      5. 位置通知：触发 on_position_change 回调
    """

    # ── 物理常数（以 60fps 为基准）──────────────────────────────
    GRAVITY: float          = 0.55  # 重力加速度（像素/帧²）
    BOUNCE_VY_RETAIN: float = 0.45  # 触地垂直弹力保留比例（恢复系数 e）
    BOUNCE_VX_RETAIN: float = 0.80  # 触地水平速度保留比例（地面摩擦系数）
    MIN_BOUNCE_VY: float    = 1.5   # 触地反弹速度阈值（低于则视为停止）

    # 从配置文件读取空气阻力参数
    AIR_RESISTANCE: float   = PHYSICS.get('air_resistance', 0.995)  # 每帧保留速度比例
    MIN_VELOCITY: float     = PHYSICS.get('min_velocity', 0.1)      # 静止速度阈值
    TICK_SUBSTEPS: int      = max(1, round(60 / 20))

    def __init__(self) -> None:
        self._bodies: list[PhysicsBody] = []
        self._pending_future = None

        # 多屏环境使用虚拟桌面边界
        self._screen_left: int = 0
        self._screen_right: int = 0
        self._screen_top: int = 0
        self._screen_bottom: int = 0
        self._refresh_screen_bounds()

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        _log("物理世界已初始化")

    # ── 公开接口 ──────────────────────────────────────────────────

    def add_body(self, body: PhysicsBody) -> None:
        """注册物理体（幂等）。"""
        if body not in self._bodies:
            self._bodies.append(body)

    def remove_body(self, body: PhysicsBody) -> None:
        """注销物理体（幂等，重复调用安全）。"""
        if body in self._bodies:
            self._bodies.remove(body)

    def cleanup(self) -> None:
        """注销事件订阅，清空所有物理体（通常在应用退出时调用）。"""
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._bodies.clear()
        _log("物理世界已清理")

    # ── 帧更新 ────────────────────────────────────────────────────

    def _on_tick(self, event: Event) -> None:
        """TICK 事件回调：应用上一 tick 结果，并提交下一 tick 批量计算。"""
        self._refresh_screen_bounds()
        self._apply_pending_updates()
        self._submit_frame_job()

    def _on_frame(self, event: Event) -> None:
        """FRAME 事件回调：按 tick alpha 插值显示位置。"""
        alpha = float((event.data or {}).get("tick_alpha", 1.0) or 0.0)
        alpha = max(0.0, min(1.0, alpha))
        for body in list(self._bodies):
            if not body.active:
                body.prev_x = body.x
                body.prev_y = body.y
                body.render_x = body.x
                body.render_y = body.y
                if body.on_position_change:
                    body.on_position_change(body)
                continue
            body.render_x = body.prev_x + (body.x - body.prev_x) * alpha
            body.render_y = body.prev_y + (body.y - body.prev_y) * alpha
            if body.on_position_change:
                body.on_position_change(body)

    def _build_snapshot(self) -> list[dict]:
        snapshot: list[dict] = []
        for body in list(self._bodies):
            if not body.active:
                continue
            snapshot.append({
                "body": body,
                "x": body.x,
                "y": body.y,
                "vx": body.vx,
                "vy": body.vy,
                "ground_y": body.ground_y,
                "width": body.width,
                "height": body.height,
                "max_bounces": body.max_bounces,
                "bounce_count": body.bounce_count,
                "gravity_enabled": body.gravity_enabled,
                "active": body.active,
                "bounce_vx_retain": body.bounce_vx_retain,
            })
        return snapshot

    def _submit_frame_job(self) -> None:
        snapshot = self._build_snapshot()
        if not snapshot:
            return
        future = get_compute_hub().submit_latest(
            "physics_world_step",
            _step_physics_batch,
            snapshot,
            gravity=self.GRAVITY,
            bounce_vy_retain=self.BOUNCE_VY_RETAIN,
            bounce_vx_retain=self.BOUNCE_VX_RETAIN,
            min_bounce_vy=self.MIN_BOUNCE_VY,
            min_velocity=self.MIN_VELOCITY,
            screen_left=self._screen_left,
            screen_right=self._screen_right,
            screen_top=self._screen_top,
            screen_bottom=self._screen_bottom,
            substeps=self.TICK_SUBSTEPS,
            executor="vector",
        )
        if future is not None:
            self._pending_future = future

    def _apply_pending_updates(self) -> None:
        future = self._pending_future
        if future is None or not future.done():
            return
        self._pending_future = None
        try:
            updates = future.result()
        except Exception as exc:
            _log(f"后台物理步进异常: {exc}")
            return

        for update in updates:
            body: PhysicsBody = update["body"]
            if body not in self._bodies:
                continue
            body.prev_x = body.x
            body.prev_y = body.y
            body.x = float(update["x"])
            body.y = float(update["y"])
            body.render_x = body.x
            body.render_y = body.y
            body.vx = float(update["vx"])
            body.vy = float(update["vy"])
            body.active = bool(update["active"])
            body.bounce_count = int(update["bounce_count"])

            side = update.get("wall_hit_side")
            if side and body.on_wall_hit:
                body.on_wall_hit(body, side)

            ground_stopped = update.get("ground_stopped")
            if ground_stopped is not None and body.on_ground_bounce:
                body.on_ground_bounce(body, bool(ground_stopped))

    def _refresh_screen_bounds(self) -> None:
        """刷新当前虚拟桌面边界。"""
        geom = get_virtual_screen_geometry()
        self._screen_left = geom.x()
        self._screen_top = geom.y()
        self._screen_right = geom.x() + geom.width()
        self._screen_bottom = geom.y() + geom.height()

# ══════════════════════════════════════════════════════════════════════
# 全局单例
# ══════════════════════════════════════════════════════════════════════

_world: Optional[PhysicsWorld] = None


def _on_pre_start(event: Event) -> None:
    """预启动事件回调：初始化物理世界。"""
    global _world
    if _world is None:
        _world = PhysicsWorld()
    # 取消订阅，只需初始化一次
    get_event_center().unsubscribe(EventType.APP_PRE_START, _on_pre_start)


def get_physics_world() -> PhysicsWorld:
    """获取全局物理世界单例（懒初始化，线程不安全，仅限主线程使用）。"""
    global _world
    if _world is None:
        _world = PhysicsWorld()
    return _world


def cleanup_physics_world() -> None:
    """清理全局物理世界（应用退出时调用）。"""
    global _world
    if _world is not None:
        _world.cleanup()
        _world = None


# 订阅预启动事件，在应用启动时初始化物理世界
get_event_center().subscribe(EventType.APP_PRE_START, _on_pre_start)
