"""动作定义模块 - 定义所有宠物动作及其属性"""
from enum import Enum


class ActionInterruptMode(Enum):
    """动作打断模式"""
    NONE = "none"          # 不可打断
    EVENT = "event"        # 只能被事件打断
    ANY = "any"            # 可以被任何方式打断


class Action:
    """动作定义类"""

    def __init__(self, name: str, repeat_count: int = 1, interrupt_mode: ActionInterruptMode = ActionInterruptMode.ANY, group: str = None, particle_config=None):
        """
        初始化动作

        Args:
            name: 动作名称（对应GIF文件名）
            repeat_count: 默认播放次数（-1表示无限循环）
            interrupt_mode: 打断模式
            group: 动作分组
            particle_config: 粒子效果配置（字典），格式：
                {
                    'particle_id': 'scatter_fall',  # 粒子ID
                    'area_type': 'point',           # 区域类型
                    'trigger': 'start'              # 触发时机：'start'（开始时）或 'end'（结束时）
                }
        """
        self.name = name
        self.repeat_count = repeat_count
        self.interrupt_mode = interrupt_mode
        self.group = group
        self.particle_config = particle_config or None

    def has_particle_effect(self) -> bool:
        """检查是否配置了粒子效果"""
        return self.particle_config is not None


class Actions:
    """所有动作定义"""

    # 基础动作 - stay类型（只能被事件打断）
    IDLE = Action("idle", repeat_count=-1, interrupt_mode=ActionInterruptMode.EVENT, group="base")
    MOVING = Action("moving", repeat_count=-1, interrupt_mode=ActionInterruptMode.EVENT, group="base")

    # 短暂动作 - 播放2次后自动结束
    BORING = Action("boring", repeat_count=2, interrupt_mode=ActionInterruptMode.NONE)
    WAVE = Action("wave", repeat_count=2, interrupt_mode=ActionInterruptMode.NONE, group="action1")
    JUMPING = Action("jumping", repeat_count=2, interrupt_mode=ActionInterruptMode.NONE, group="action1", particle_config={'particle_id': 'scatter_fall', 'area_type': 'point', 'trigger': 'start'})
    HAPPY = Action("happy", repeat_count=2, interrupt_mode=ActionInterruptMode.NONE, group="action1", particle_config={'particle_id': 'pink_scatter_fall', 'area_type': 'point', 'trigger': 'start'})
    PLAY = Action("play", repeat_count=2, interrupt_mode=ActionInterruptMode.NONE)

    # 动作分组定义
    ACTION_GROUPS = {
        "base": ["idle", "moving"],
        "action1": ["happy", "wave", "jumping"],
    }

    @classmethod
    def get_action(cls, name: str) -> Action:
        """
        根据名称获取动作定义

        Args:
            name: 动作名称

        Returns:
            Action对象，如果不存在则返回None
        """
        return getattr(cls, name.upper(), None)

    @classmethod
    def get_all_actions(cls) -> dict[str, Action]:
        """
        获取所有动作定义

        Returns:
            动作名称到Action对象的字典
        """
        actions = {}
        for attr_name in dir(cls):
            if not attr_name.startswith('_') and attr_name.isupper():
                action = getattr(cls, attr_name)
                if isinstance(action, Action):
                    actions[attr_name.lower()] = action
        return actions

    @classmethod
    def get_actions_by_group(cls, group: str) -> list[Action]:
        """
        根据分组获取动作列表

        Args:
            group: 分组名称

        Returns:
            该分组下的所有Action对象列表
        """
        actions = []
        for attr_name in dir(cls):
            if not attr_name.startswith('_') and attr_name.isupper():
                action = getattr(cls, attr_name)
                if isinstance(action, Action) and action.group == group:
                    actions.append(action)
        return actions

    @classmethod
    def get_random_action_from_group(cls, group: str) -> Action:
        """
        从指定分组中随机选择一个动作

        Args:
            group: 分组名称

        Returns:
            随机选择的Action对象，如果分组为空则返回None
        """
        import random
        actions = cls.get_actions_by_group(group)
        if actions:
            return random.choice(actions)
        return None

    @classmethod
    def is_stay_action(cls, action: Action) -> bool:
        """
        判断是否为stay类型动作（只能被事件打断）

        Args:
            action: 动作对象

        Returns:
            是否为stay类型
        """
        return action.interrupt_mode == ActionInterruptMode.EVENT

    @classmethod
    def is_interruptible(cls, action: Action, by_event: bool = True) -> bool:
        """
        判断动作是否可以被打断

        Args:
            action: 动作对象
            by_event: 是否由事件触发打断

        Returns:
            是否可以被打断
        """
        if action.interrupt_mode == ActionInterruptMode.NONE:
            return False
        elif action.interrupt_mode == ActionInterruptMode.EVENT:
            return by_event
        else:  # ANY
            return True
