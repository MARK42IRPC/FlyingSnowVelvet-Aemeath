"""键盘事件处理器 - 处理键盘相关的具体逻辑"""
import random

from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.input.types import Key


class KeyEventHandler:
    """处理键盘事件的具体逻辑"""

    def __init__(self, entity):
        """
        初始化键盘事件处理器

        Args:
            entity: BaseEntity实例(PetWindow)
        """
        self._entity = entity
        self._event_center = get_event_center()

        # 订阅键盘事件
        self._event_center.subscribe(EventType.KEY_PRESS, self._on_key_press)

    def _on_key_press(self, event: Event):
        """处理键盘按下"""
        key = event.data.get('key')

        # 空格键：触发随机动作
        if key == Key.SPACE:
            if not self._entity.is_moving():
                state = random.choice(BEHAVIOR['random_states'])
                self._entity.play_animation(state, duration=random.randint(2000, 4000))

        # 方向键不再驱动主宠移动；仅由各 UI 组件按需处理。
