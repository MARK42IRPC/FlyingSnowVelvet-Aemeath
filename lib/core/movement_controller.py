"""
移动控制器模块 - 管理实体的移动逻辑

将移动相关的状态和算法从 PetWindow 中抽离，形成独立的移动控制器。
支持速度插值、方向翻转、目标追踪等功能。
"""

from typing import Optional, Callable
from PyQt5.QtCore import QPoint

from config.config import BEHAVIOR


class MovementController:
    """
    移动控制器 - 管理实体的移动状态和算法
    
    职责：
    - 管理移动状态（是否正在移动、目标位置、当前速度）
    - 实现速度插值算法（加速、减速）
    - 处理方向翻转
    - 计算每帧的位移
    
    不负责：
    - 实际的窗口移动（由调用者执行）
    - 状态机的状态切换（由调用者通过回调触发）
    """

    def __init__(self,
                 on_position_update: Optional[Callable[[QPoint], None]] = None,
                 on_move_complete: Optional[Callable[[], None]] = None,
                 on_direction_change: Optional[Callable[[bool], None]] = None):
        """
        初始化移动控制器
        
        Args:
            on_position_update: 位置更新回调，参数为新位置
            on_move_complete: 移动完成回调
            on_direction_change: 方向改变回调，参数为是否翻转
        """
        # 移动状态
        self._moving = False
        self._target = QPoint(0, 0)
        self._current_speed = BEHAVIOR['move_min_speed']
        self._arrival_radius = 1.0
        self._prev_x = 0.0
        self._prev_y = 0.0
        self._current_x = 0.0
        self._current_y = 0.0
        self._render_x = 0.0
        self._render_y = 0.0
        
        # 方向
        self._flipped = False
        
        # 回调
        self._on_position_update = on_position_update
        self._on_move_complete = on_move_complete
        self._on_direction_change = on_direction_change

    # ==================================================================
    # 属性访问器
    # ==================================================================

    @property
    def is_moving(self) -> bool:
        """是否正在移动"""
        return self._moving

    @property
    def target(self) -> QPoint:
        """当前目标位置"""
        return self._target

    @property
    def flipped(self) -> bool:
        """当前朝向（是否翻转）"""
        return self._flipped

    @flipped.setter
    def flipped(self, value: bool):
        """设置朝向"""
        if self._flipped != value:
            self._flipped = value
            if self._on_direction_change:
                self._on_direction_change(value)

    # ==================================================================
    # 移动控制
    # ==================================================================

    def start_move(self, target: QPoint, arrival_radius: float = 1.0) -> None:
        """
        开始移动到目标位置
        
        Args:
            target: 目标位置（全局坐标）
        """
        self._target = target
        self._moving = True
        # 速度从最低开始
        self._current_speed = BEHAVIOR['move_min_speed']
        try:
            self._arrival_radius = max(1.0, float(arrival_radius))
        except (TypeError, ValueError):
            self._arrival_radius = 1.0

    def update_target(self, target: QPoint, arrival_radius: float | None = None) -> None:
        """
        动态更新移动目标点（仅在移动中生效）
        
        用于追踪动态目标（如跳跃中的雪豹）时持续刷新目标位置。
        
        Args:
            target: 新的目标位置
        """
        if self._moving:
            self._target = target
            if arrival_radius is not None:
                try:
                    self._arrival_radius = max(1.0, float(arrival_radius))
                except (TypeError, ValueError):
                    pass

    def stop_move(self) -> None:
        """停止移动"""
        self._moving = False
        self._flipped = False
        self._current_speed = BEHAVIOR['move_min_speed']
        self._arrival_radius = 1.0
        self._prev_x = self._current_x
        self._prev_y = self._current_y
        self._render_x = self._current_x
        self._render_y = self._current_y

    def sync_position(self, pos: QPoint) -> None:
        """将真实位置与渲染位置同步到指定坐标。"""
        self._prev_x = float(pos.x())
        self._prev_y = float(pos.y())
        self._current_x = float(pos.x())
        self._current_y = float(pos.y())
        self._render_x = float(pos.x())
        self._render_y = float(pos.y())

    # ==================================================================
    # 帧更新
    # ==================================================================

    def update_tick(self) -> None:
        """
        TICK 事件更新 - 更新真实位置与速度
        """
        if not self._moving:
            return

        dx = self._target.x() - self._current_x
        dy = self._target.y() - self._current_y
        dist = (dx**2 + dy**2) ** 0.5

        self._prev_x = self._current_x
        self._prev_y = self._current_y

        # 到达目标
        if dist <= self._arrival_radius:
            self._moving = False
            self._flipped = False
            self._current_speed = BEHAVIOR['move_min_speed']
            self._arrival_radius = 1.0
            self._render_x = self._current_x
            self._render_y = self._current_y
            if self._on_move_complete:
                self._on_move_complete()
            return

        # 更新方向
        new_flipped = dx < 0
        if self._flipped != new_flipped:
            self._flipped = new_flipped
            if self._on_direction_change:
                self._on_direction_change(new_flipped)

        # 速度插值逻辑
        self._update_speed(dist)

        speed = max(self._current_speed, BEHAVIOR['move_min_speed'])
        move_distance = min(speed, dist)
        self._current_x += dx / dist * move_distance
        self._current_y += dy / dist * move_distance

    def update_frame(self, alpha: float) -> Optional[QPoint]:
        """
        FRAME 事件更新 - 按 tick alpha 插值渲染位置。
        """
        alpha = max(0.0, min(1.0, float(alpha)))
        self._render_x = self._prev_x + (self._current_x - self._prev_x) * alpha
        self._render_y = self._prev_y + (self._current_y - self._prev_y) * alpha
        nx = round(self._render_x)
        ny = round(self._render_y)
        new_pos = QPoint(nx, ny)

        if self._on_position_update:
            self._on_position_update(new_pos)

        return new_pos

    def _update_speed(self, dist: float) -> None:
        """
        更新移动速度（内部方法）
        
        速度插值逻辑：
        - 在减速范围内：速度接近最低时尝试加速，高于最低时减速
        - 不在减速范围：持续加速直到最高速度
        
        Args:
            dist: 当前距离目标的距离
        """
        decel_distance = BEHAVIOR['move_decel_distance']
        min_speed = BEHAVIOR['move_min_speed']
        max_speed = BEHAVIOR['move_max_speed']
        acceleration = BEHAVIOR['move_acceleration']

        if dist <= decel_distance:
            # 在减速范围内
            if self._current_speed > min_speed:
                # 速度大于最低速度，尝试减速
                self._current_speed -= acceleration
                if self._current_speed < min_speed:
                    self._current_speed = min_speed
            else:
                # 速度小于等于最低速度，尝试加速
                self._current_speed += acceleration
                if self._current_speed > max_speed:
                    self._current_speed = max_speed
        else:
            # 不在减速范围内，持续加速
            if self._current_speed < max_speed:
                self._current_speed += acceleration
                if self._current_speed > max_speed:
                    self._current_speed = max_speed

    # ==================================================================
    # 状态重置
    # ==================================================================

    def reset(self) -> None:
        """重置移动状态"""
        self._moving = False
        self._flipped = False
        self._current_speed = BEHAVIOR['move_min_speed']
        self._arrival_radius = 1.0
        self._target = QPoint(0, 0)
        self._prev_x = 0.0
        self._prev_y = 0.0
        self._current_x = 0.0
        self._current_y = 0.0
        self._render_x = 0.0
        self._render_y = 0.0
