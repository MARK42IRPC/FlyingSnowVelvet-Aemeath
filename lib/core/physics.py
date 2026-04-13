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

from typing import Callable, Optional

from PyQt5.QtWidgets import QApplication

from lib.core.event.center import get_event_center, EventType, Event
from config.config import PHYSICS
from lib.core.logger import get_logger
from lib.core.screen_utils import get_virtual_screen_geometry

_logger = get_logger(__name__)


def _log(msg: str) -> None:
    _logger.debug("[PhysicsWorld] %s", msg)


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

    def __init__(self) -> None:
        self._bodies: list[PhysicsBody] = []

        # 多屏环境使用虚拟桌面边界
        self._screen_left: int = 0
        self._screen_right: int = 0
        self._screen_top: int = 0
        self._screen_bottom: int = 0
        self._refresh_screen_bounds()

        self._event_center = get_event_center()
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
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._bodies.clear()
        _log("物理世界已清理")

    # ── 帧更新 ────────────────────────────────────────────────────

    def _on_frame(self, event: Event) -> None:
        """FRAME 事件回调：遍历所有活跃物理体并执行步进。"""
        self._refresh_screen_bounds()
        for body in list(self._bodies):
            if body.active:
                self._step(body)

    def _refresh_screen_bounds(self) -> None:
        """刷新当前虚拟桌面边界。"""
        geom = get_virtual_screen_geometry()
        self._screen_left = geom.x()
        self._screen_top = geom.y()
        self._screen_right = geom.x() + geom.width()
        self._screen_bottom = geom.y() + geom.height()

    def _step(self, body: PhysicsBody) -> None:
        """单物理体一帧步进（顺序：重力 → 移动 → 边界碰撞 → 地面碰撞 → 空气阻力 → 通知）。"""

        # 1. 施加重力（仅在重力开启时）
        if body.gravity_enabled:
            body.vy += self.GRAVITY

        # 2. 移动
        body.x += body.vx
        body.y += body.vy

        # 3. 左/右屏幕边界碰撞
        left_limit  = self._screen_left
        right_limit = self._screen_right - body.width  # 右边界以窗口左上角为准

        if body.x <= left_limit:
            body.x  = float(left_limit)
            body.vx = abs(body.vx)        # 反转为正方向（向右）
            if body.on_wall_hit:
                body.on_wall_hit(body, 'left')
        elif body.x >= right_limit:
            body.x  = float(right_limit)
            body.vx = -abs(body.vx)       # 反转为负方向（向左）
            if body.on_wall_hit:
                body.on_wall_hit(body, 'right')

        # 4. 地面碰撞（仅在重力开启时处理）
        if body.gravity_enabled and body.y >= body.ground_y:
            body.y   = body.ground_y
            body.vy  = -abs(body.vy) * self.BOUNCE_VY_RETAIN   # 垂直反弹（弹性衰减）
            # 水平摩擦：优先使用 per-body 覆盖值，无则使用世界常数
            vx_retain = body.bounce_vx_retain if body.bounce_vx_retain is not None else self.BOUNCE_VX_RETAIN
            body.vx *= vx_retain
            body.bounce_count += 1

            # 弹力不足 或 已达最大弹跳次数 → 停止
            stopped = (
                abs(body.vy) < self.MIN_BOUNCE_VY
                or body.bounce_count >= body.max_bounces
            )
            if stopped:
                body.vy     = 0.0
                body.vx     = 0.0
                body.active = False

            if body.on_ground_bounce:
                body.on_ground_bounce(body, stopped)

        # 5. 上下边界碰撞（仅在重力关闭时处理）
        if not body.gravity_enabled:
            top_limit    = self._screen_top
            bottom_limit = self._screen_bottom - body.height  # 下边界以窗口左上角为准

            if body.y <= top_limit:
                body.y  = float(top_limit)
                body.vy = abs(body.vy)        # 反转为正方向（向下）
                if body.on_wall_hit:
                    body.on_wall_hit(body, 'top')
            elif body.y >= bottom_limit:
                body.y  = float(bottom_limit)
                body.vy = -abs(body.vy)       # 反转为负方向（向上）
                if body.on_wall_hit:
                    body.on_wall_hit(body, 'bottom')

        # 6. 空气阻力（每帧衰减速度，仅在物理体仍活跃时）
        # 速度越快，阻力越大：resistance = 0.995 - (speed_factor * 0.035)
        # speed_factor 归一化到 [0, 1]，最终 resistance ∈ [0.96, 0.995]
        if body.active:
            speed = (body.vx ** 2 + body.vy ** 2) ** 0.5
            # 以速度 30 为基准进行归一化（速度 >= 30 时阻力最大）
            speed_factor = min(speed / 30.0, 1.0)
            # 计算动态阻力系数：速度越快越接近 0.96，速度越慢越接近 0.995
            dynamic_resistance = 0.995 - (speed_factor * 0.035)

            body.vx *= dynamic_resistance
            body.vy *= dynamic_resistance

            # 7. 静止检测（空气阻力将速度耗尽至极低时停止）
            # 重力开启时仅在贴近地面（y >= ground_y - 1.0）时判定，
            # 避免弹跳最高点速度趋近零时被误判为静止而悬停在空中
            near_ground = (not body.gravity_enabled) or (body.y >= body.ground_y - 1.0)
            if near_ground and speed < self.MIN_VELOCITY:
                body.vx     = 0.0
                body.vy     = 0.0
                body.active = False

        # 8. 位置变化通知
        if body.on_position_change:
            body.on_position_change(body)


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
