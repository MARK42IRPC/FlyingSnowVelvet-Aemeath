"""鼠标事件处理器 - 处理鼠标相关的具体逻辑"""
from PyQt5.QtCore import Qt

from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.voice.ams_enh import AmsEnhSound


class MouseEventHandler:
    """处理鼠标事件的具体逻辑"""

    def __init__(self, entity):
        """
        初始化鼠标事件处理器

        Args:
            entity: BaseEntity实例(PetWindow)
        """
        self._entity = entity
        self._event_center = get_event_center()
        self._drag_offset = None

        # ams-enh 音效（interruptible=False：不被其他音效打断）
        # CD 由 AmsEnhSound 内部 TICK 计数管理（20 tick = 1000ms）
        self._ams_enh = AmsEnhSound(interruptible=False)

        # 订阅鼠标事件
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._on_mouse_move)

    def _on_mouse_press(self, event: Event):
        """处理鼠标按下"""
        button = event.data.get('button')
        global_pos = event.data.get('global_pos')
        pos = event.data.get('pos')

        if button == Qt.LeftButton:
            # 粒子特效（使用全局坐标）
            gpos = self._entity.mapToGlobal(pos)
            self._entity.spawn_particles(gpos.x(), gpos.y(), particle_id='cyan_pink_scatter_fall')

            # 记录拖拽偏移
            self._drag_offset = global_pos - self._entity.get_position()

            # ams-enh 音效（CD 由 AmsEnhSound 内部控制，直接调用即可）
            self._ams_enh.play()

        elif button == Qt.RightButton:
            # 粒子特效（使用全局坐标）
            gpos = self._entity.mapToGlobal(pos)
            self._entity.spawn_particles(gpos.x(), gpos.y(), particle_id='pink_scatter_fall')

            # 发布切换命令框事件（打开/关闭右键UI）
            self._event_center.publish(Event(EventType.UI_COMMAND_TOGGLE, {
                'entity': self._entity
            }))

    def _on_mouse_move(self, event: Event):
        """处理鼠标移动"""
        buttons = event.data.get('buttons')
        global_pos = event.data.get('global_pos')

        if buttons & Qt.LeftButton and self._drag_offset:
            new_pos = global_pos - self._drag_offset
            self._entity.move(new_pos)

            # 发布锚点更新事件，通知 UI 组件更新位置
            self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
                'window_id':   'pet_window',
                'anchor_id':   'all',
                'anchor_point': new_pos,
                'ui_id':       'all'
            }))
