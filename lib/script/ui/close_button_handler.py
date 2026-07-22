"""关闭按钮事件处理器"""
from PyQt5.QtCore import Qt

from lib.core.event.center import get_event_center, EventType, Event


class CloseButtonEventHandler:
    """处理关闭按钮的事件逻辑"""

    def __init__(self, close_button):
        self._button = close_button
        self._event_center = get_event_center()

        # 订阅鼠标进入和离开事件
        self._event_center.subscribe(EventType.MOUSE_ENTER, self._on_mouse_enter)
        self._event_center.subscribe(EventType.MOUSE_LEAVE, self._on_mouse_leave)
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)

    def _on_mouse_enter(self, event: Event):
        """处理鼠标进入"""
        pet = event.data.get('pet')
        if pet == self._button.parent():
            self._button.fade_in()

    def _on_mouse_leave(self, event: Event):
        """处理鼠标离开"""
        pet = event.data.get('pet')
        if pet == self._button.parent():
            self._button.fade_out()

    def _on_mouse_press(self, event: Event):
        """处理鼠标按下"""
        if not getattr(self._button, "_visible", False):
            return
        if not getattr(self._button, "_anchor_available", False):
            return

        button = event.data.get('button')
        global_pos = event.data.get('global_pos')

        # 只处理左键点击
        if button == Qt.LeftButton:
            # 检查点击是否在按钮上
            button_rect = self._button.geometry()
            button_global_pos = self._button.mapToGlobal(self._button.rect().topLeft())

            if (button_global_pos.x() <= global_pos.x() <= button_global_pos.x() + button_rect.width() and
                button_global_pos.y() <= global_pos.y() <= button_global_pos.y() + button_rect.height()):
                # 点击在按钮上，执行点击操作
                self._button.click()

                # 发布关闭按钮点击事件
                close_event = Event(EventType.UI_CLOSE_BUTTON_CLICK, {
                    'button': self._button
                })
                self._event_center.publish(close_event)

                # 标记事件已处理
                event.mark_handled()
