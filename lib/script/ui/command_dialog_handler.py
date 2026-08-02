"""命令对话框事件处理器。"""

from lib.core.event.center import get_event_center, EventType, Event
from lib.core.graphics.types import coerce_point
from lib.core.input.types import MouseButton


class CommandDialogEventHandler:
    """处理命令对话框的事件逻辑"""

    def __init__(self, command_dialog):
        self._dialog = command_dialog
        self._event_center = get_event_center()

        # 订阅鼠标按下事件（用于点击输入框时夺回焦点）
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)

    def _on_mouse_press(self, event: Event):
        """处理鼠标按下 - 点击对话框区域时让输入框获取焦点"""
        button = event.data.get('button')
        global_pos = coerce_point(event.data.get('global_pos'))

        # 只处理左键点击，且对话框可见时
        if button == MouseButton.LEFT and global_pos is not None and self._dialog.isVisible():
            # 检查点击是否在对话框窗口上
            dialog_rect = self._dialog.geometry()
            dialog_global_pos = self._dialog.mapToGlobal(self._dialog.rect().topLeft())

            if (dialog_global_pos.x() <= global_pos.x <= dialog_global_pos.x() + dialog_rect.width() and
                dialog_global_pos.y() <= global_pos.y <= dialog_global_pos.y() + dialog_rect.height()):
                # 点击在对话框上，让输入框获取焦点
                self._dialog._entry.setFocus()
                # 标记事件已处理，避免传递给其他处理器
                event.mark_handled()
