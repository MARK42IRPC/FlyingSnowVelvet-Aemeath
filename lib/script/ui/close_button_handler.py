"""关闭按钮事件处理器。"""

from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals.types import coerce_point
from lib.core.input.types import MouseButton


class CloseButtonEventHandler:
    """处理关闭按钮的事件逻辑"""

    def __init__(self, close_button):
        self._button = close_button
        self._event_center = get_event_center()

        # 订阅鼠标进入和离开事件
        self._event_center.subscribe(EventType.MOUSE_ENTER, self._on_mouse_enter)
        self._event_center.subscribe(EventType.MOUSE_LEAVE, self._on_mouse_leave)
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)

    def _owner_window(self):
        """按钮所属的原生窗口。

        按钮迁出 QWidget 后不再有 ``parent()``，它真正的窗口是后端宿主；这里
        统一取宿主，未迁移的控件仍回退到 Qt 的 ``parent()``。
        """
        runtime = getattr(self._button, '_runtime', None)
        host = getattr(runtime, 'host', None)
        if host is not None:
            return host
        parent = getattr(self._button, 'parent', None)
        return parent() if callable(parent) else None

    def _on_mouse_enter(self, event: Event):
        """处理鼠标进入"""
        pet = event.data.get('pet')
        if pet == self._owner_window():
            self._button.fade_in()

    def _on_mouse_leave(self, event: Event):
        """处理鼠标离开"""
        pet = event.data.get('pet')
        if pet == self._owner_window():
            self._button.fade_out()

    def _on_mouse_press(self, event: Event):
        """处理鼠标按下"""
        if not getattr(self._button, "_visible", False):
            return
        if not getattr(self._button, "_anchor_available", False):
            return

        button = event.data.get('button')
        global_pos = coerce_point(event.data.get('global_pos'))

        # 只处理左键点击
        if button == MouseButton.LEFT and global_pos is not None:
            # 检查点击是否在按钮上；控件已不是 QWidget，几何从后端宿主取。
            runtime = getattr(self._button, '_runtime', None)
            host = getattr(runtime, 'host', None) or self._button
            button_rect = host.geometry()
            button_global_pos = host.mapToGlobal(host.rect().topLeft())

            if (button_global_pos.x() <= global_pos.x <= button_global_pos.x() + button_rect.width() and
                button_global_pos.y() <= global_pos.y <= button_global_pos.y() + button_rect.height()):
                # 点击在按钮上，执行点击操作
                self._button.click()

                # 发布关闭按钮点击事件
                close_event = Event(EventType.UI_CLOSE_BUTTON_CLICK, {
                    'button': self._button
                })
                self._event_center.publish(close_event)

                # 标记事件已处理
                event.mark_handled()
