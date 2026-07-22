"""键盘事件处理器 - 处理键盘相关的具体逻辑"""
import random
from PyQt5.QtCore import Qt

from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event


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

        # 播放列表打开时，左右键优先用于移动队列项（不再驱动主宠移动）
        if key in (Qt.Key_Left, Qt.Key_Right):
            try:
                from lib.script.ui.playlist_panel import get_playlist_panel
                panel = get_playlist_panel()
                if panel is not None and panel.is_visible:
                    direction = -1 if key == Qt.Key_Left else 1
                    if panel.move_selected_by_key(direction):
                        event.mark_handled()
                        return
            except Exception:
                pass

        # 空格键：触发随机动作
        if key == Qt.Key_Space:
            if not self._entity.is_moving():
                state = random.choice(BEHAVIOR['random_states'])
                self._entity.play_animation(state, duration=random.randint(2000, 4000))

        # 方向键不再驱动主宠移动；仅由各 UI 组件按需处理。
