"""实体基类模块 - 定义统一的调度接口"""
from abc import ABC, abstractmethod

from lib.core.graphics.types import Point, Rect, coerce_point, coerce_rect


class BaseEntity(ABC):
    """
    实体基类 - 所有游戏对象的抽象父类
    定义统一的调度接口,消除越级调用
    """

    # ==================================================================
    # 状态管理接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def change_state(self, state: str):
        """切换到指定状态"""
        pass

    @abstractmethod
    def get_current_state(self) -> str:
        """获取当前状态"""
        pass

    # ==================================================================
    # 移动接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def start_move(self, target: Point):
        """开始移动到目标位置"""
        pass

    @abstractmethod
    def stop_move(self):
        """停止移动"""
        pass

    @abstractmethod
    def get_position(self):
        """获取后端兼容位置；核心逻辑优先使用 get_core_position。"""
        pass

    def get_core_position(self) -> Point:
        """获取与后端无关的当前位置；Qt 兼容层可继续覆盖 get_position。"""
        return coerce_point(self.get_position()) or Point()

    # ==================================================================
    # 动画接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def play_animation(self, state: str, duration: int = 0):
        """播放指定动画,可选持续时间"""
        pass

    # ==================================================================
    # 粒子接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def spawn_particles(self, x: int, y: int, particle_id: str = 'scatter_fall', area_type: str = 'point', area_data=None):
        """
        在指定位置生成粒子效果（通过事件中心发布申请）

        Args:
            x: X 坐标
            y: Y 坐标
            particle_id: 粒子ID（如 'scatter_fall', 'heart'）
            area_type: 区域类型（'point', 'rect', 'circle'）
            area_data: 区域数据
                - 如果是 'rect': (x1, y1, x2, y2) 矩形范围
                - 如果是 'circle': (x, y, radius) 圆形范围
                - 如果是 'point' 或 None: 使用 (x, y) 作为单点
        """
        pass

    # ==================================================================
    # UI组件接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def toggle_command_dialog(self):
        """切换命令对话框显示状态"""
        pass

    # ==================================================================
    # 子对象管理接口 - 默认实现
    # ==================================================================

    def spawn_child(self, child_type: str, **kwargs):
        """
        生成子对象（默认实现：不做任何操作）

        Args:
            child_type: 子对象类型
            **kwargs: 子对象参数
        """
        pass

    def dismiss_child(self, child):
        """解散/移除子对象（默认实现：不做任何操作）"""
        pass

    def get_children(self, child_type: str = None):
        """
        获取子对象列表（默认实现：返回空列表）

        Args:
            child_type: 可选,按类型筛选

        Returns:
            子对象列表
        """
        return []

    # ==================================================================
    # 计时器接口 - 子类必须实现
    # ==================================================================

    # ==================================================================
    # 计时器接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def schedule_task(self, callback, delay_ms: int, repeat: bool = False):
        """
        调度任务

        Args:
            callback: 回调函数
            delay_ms: 延迟时间(毫秒)
            repeat: 是否重复

        Returns:
            任务ID
        """
        pass

    @abstractmethod
    def cancel_task(self, task_id: str):
        """取消任务"""
        pass

    # ==================================================================
    # 位置查询接口 - 子类必须实现
    # ==================================================================

    @abstractmethod
    def get_geometry(self):
        """获取窗口几何信息"""
        pass

    def get_core_geometry(self) -> Rect:
        """获取与后端无关的窗口矩形。"""
        return coerce_rect(self.get_geometry()) or Rect()

    @abstractmethod
    def is_moving(self) -> bool:
        """是否正在移动"""
        pass

    @abstractmethod
    def set_direction(self, flipped: bool):
        """设置朝向(翻转)"""
        pass

    @abstractmethod
    def get_direction(self) -> bool:
        """获取朝向(是否翻转)"""
        pass
