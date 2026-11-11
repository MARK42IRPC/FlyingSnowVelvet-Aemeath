"""动作定义的向后兼容适配层。

动作表的事实源是 `config/actions.py` 的 `ACTIONS`。本模块只做两件事：

1. 把 `ActionSpec` 适配成历史 `Action` 接口（`name` / `repeat_count` /
   `interrupt_mode` / `group` / `particle_config`），保持既有调用面可用；
2. 提供 `Actions` 门面，转发到动作表查询函数。

新代码应直接使用 `config.actions` 的 `action_spec()` / `actions_in_group()`，
不要依赖这里的 `ActionInterruptMode` 枚举。
"""
from __future__ import annotations

import random
from enum import Enum

from config.actions import (
    ACTION,
    ACTIONS,
    BASE,
    INTERRUPT_ANY,
    INTERRUPT_EVENT,
    INTERRUPT_NONE,
    TRANSIENT,
    ActionSpec,
    action_spec,
    actions_in_group,
    validate,
)


class ActionInterruptMode(Enum):
    """动作打断模式（历史接口，向后兼容保留）。"""

    NONE = "none"          # 不可打断
    EVENT = "event"        # 只能被事件打断
    ANY = "any"            # 可以被任何方式打断


_INTERRUPT_TO_MODE = {
    INTERRUPT_NONE: ActionInterruptMode.NONE,
    INTERRUPT_EVENT: ActionInterruptMode.EVENT,
    INTERRUPT_ANY: ActionInterruptMode.ANY,
}


class Action:
    """动作定义（从 `ActionSpec` 适配出的历史视图）。"""

    def __init__(
        self,
        name: str,
        repeat_count: int = 1,
        interrupt_mode: ActionInterruptMode = ActionInterruptMode.ANY,
        group: str = None,
        particle_config=None,
    ):
        self.name = name
        self.repeat_count = repeat_count
        self.interrupt_mode = interrupt_mode
        self.group = group
        self.particle_config = particle_config or None

    @classmethod
    def from_spec(cls, action_id: str, spec: ActionSpec) -> "Action":
        groups = spec.groups
        return cls(
            name=action_id,
            repeat_count=spec.loops,
            interrupt_mode=_INTERRUPT_TO_MODE[spec.interrupt],
            group=groups[0] if groups else None,
            particle_config=dict(spec.particle) if spec.particle else None,
        )

    def has_particle_effect(self) -> bool:
        """检查是否配置了粒子效果"""
        return self.particle_config is not None


class Actions:
    """动作表门面（历史接口，内部全部转发到 `config.actions`）。"""

    BASE_LAYER = BASE
    ACTION_LAYER = ACTION
    TRANSIENT_LAYER = TRANSIENT

    @classmethod
    def get_action(cls, name: str) -> Action | None:
        """按动作 id 取适配后的动作定义；不存在时返回 None。"""
        spec = action_spec(name)
        if spec is None:
            return None
        return Action.from_spec(str(name).strip(), spec)

    @classmethod
    def get_all_actions(cls) -> dict[str, Action]:
        """全部动作 id 到适配后动作定义的映射。"""
        return {aid: Action.from_spec(aid, spec) for aid, spec in ACTIONS.items()}

    @classmethod
    def get_actions_by_group(cls, group: str) -> list[Action]:
        """某语义分组下的全部动作定义。"""
        return [Action.from_spec(aid, ACTIONS[aid]) for aid in actions_in_group(group)]

    @classmethod
    def get_random_action_from_group(cls, group: str) -> Action | None:
        """从某语义分组随机取一个动作；分组为空时返回 None。"""
        actions = cls.get_actions_by_group(group)
        if actions:
            return random.choice(actions)
        return None

    @classmethod
    def get_spec(cls, name: str) -> ActionSpec | None:
        """直接取动作声明（新代码推荐入口）。"""
        return action_spec(name)

    @classmethod
    def validation_problems(cls) -> list[str]:
        """动作表问题清单；空列表表示通过（含 GIF 文件存在性检查）。"""
        return validate()

    @classmethod
    def is_stay_action(cls, action: Action) -> bool:
        """常驻层动作（idle / moving）不会自行结束，只能被事件打断。"""
        spec = action_spec(action.name)
        if spec is not None:
            return spec.layer == BASE
        return action.interrupt_mode == ActionInterruptMode.EVENT

    @classmethod
    def is_interruptible(cls, action: Action, by_event: bool = True) -> bool:
        """判断动作是否可以被打断。"""
        if action.interrupt_mode == ActionInterruptMode.NONE:
            return False
        if action.interrupt_mode == ActionInterruptMode.EVENT:
            return by_event
        return True
