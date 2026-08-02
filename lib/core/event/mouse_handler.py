"""鼠标事件处理器 - 处理核心鼠标输入。"""

from lib.core.event.center import get_event_center, EventType, Event
from lib.core.voice.ams_enh import AmsEnhSound
from lib.core.graphics.types import coerce_point
from lib.core.input.types import MouseButton, MouseButtons, MouseInput


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

        if button == MouseButton.LEFT:
            # 粒子特效（使用全局坐标）
            self._entity.spawn_particles(global_pos.x, global_pos.y, particle_id='cyan_pink_scatter_fall')

            # 记录拖拽偏移
            get_position = getattr(self._entity, "get_core_position", self._entity.get_position)
            entity_pos = coerce_point(get_position())
            self._drag_offset = global_pos - entity_pos if entity_pos is not None else None

            # ams-enh 音效（CD 由 AmsEnhSound 内部控制，直接调用即可）
            self._ams_enh.play()

        elif button == MouseButton.RIGHT:
            # 粒子特效（使用全局坐标）
            self._entity.spawn_particles(global_pos.x, global_pos.y, particle_id='pink_scatter_fall')

            # 发布切换命令框事件（打开/关闭右键UI）
            self._event_center.publish(Event(EventType.UI_COMMAND_TOGGLE, {
                'entity': self._entity
            }))

    def _on_mouse_move(self, event: Event):
        """处理鼠标移动"""
        buttons = event.data.get('buttons')
        global_pos = event.data.get('global_pos')

        if buttons & MouseButtons.LEFT and self._drag_offset:
            new_pos = global_pos - self._drag_offset
            self._entity.begin_user_drag()
            self._entity.update_user_drag_position(new_pos)

    def handle_release(self, button: MouseButton) -> None:
        """处理已经转换为核心枚举的鼠标释放事件。"""
        if button == MouseButton.LEFT:
            self._drag_offset = None
            self._entity.end_user_drag()
