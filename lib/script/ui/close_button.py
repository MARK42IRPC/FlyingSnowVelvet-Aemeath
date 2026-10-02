"""关闭按钮类"""
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QColor

from config.config import TIMEOUTS
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.script.ui.close_button_handler import CloseButtonEventHandler
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.anchor_utils import (
    refresh_last_activity,
)
from lib.script.ui.render_bridge import family_placement, move_widget_to_global, widget_global_rect
from lib.script.ui.rect_action_button_style import RectActionButton
from lib.core.render.backends.qt.widgets.anchors import get_anchor_point as resolve_anchor_point


def _hex(color: QColor) -> str:
    return color.name()


class CloseButton(RectActionButton):
    """
    关闭按钮，与输入框风格一致，对齐到输入框右上角上方4px处。
    当输入框显示时显示，输入框隐藏时隐藏。
    """

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, on_close):
        super().__init__(self.WIDTH, self.HEIGHT, TOOLTIPS['close_button'])

        self._on_close = on_close

        # 创建事件处理器
        self._event_handler = CloseButtonEventHandler(self)

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'close_button'

        # 帧事件用于位置刷新；按钮族落位统一由 RightClickUiLayer 的锚点图解算，
        # 本控件不再收发 UI_ANCHOR_RESPONSE / UI_CREATE 锚点事件（档位 2）。
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 空闲超时自动关闭功能（与 command_dialog 共享超时时间）
        self._idle_timeout = TIMEOUTS['idle_close_ms']  # 10秒无操作自动关闭
        self._last_activity_time = 0

        # 订阅鼠标事件以重置空闲计时器
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._reset_idle_timer)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._reset_idle_timer)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def get_anchor_point(self, anchor_id: str) -> QPoint:
        """
        获取指定锚点的位置

        Args:
            anchor_id: 锚点 ID ('top', 'bottom', 'left', 'right',
                        'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center')

        Returns:
            锚点位置（相对于窗口的坐标）
        """
        return resolve_anchor_point(self, anchor_id)

    def _button_text(self) -> str:
        return '关闭桌宠'

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if self._visible:
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 由 RightClickUiLayer 的锚点图给出整族矩形。"""
        rect = family_placement(self, 'close')
        if rect is None:
            return
        move_widget_to_global(self, int(rect.x), int(rect.y))

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)

        # 重置空闲计时器
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        # 在隐藏之前保存几何位置
        rect = widget_global_rect(self)

        # 设置动画完成后的回调
        self._anim.finished.connect(self._on_fade_out_complete)
        # 启动淡出动画
        self._animate(0.0)

        # 发布粒子申请事件（使用保存的位置）
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (int(rect.x), int(rect.y), int(rect.x) + int(rect.width), int(rect.y) + int(rect.height))
        })
        self._event_center.publish(particle_event)

    def _on_fade_out_complete(self):
        """淡出动画完成时的回调"""
        self._anim.finished.disconnect(self._on_fade_out_complete)
        self.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _reset_idle_timer(self, event=None):
        """重置空闲计时器"""
        refresh_last_activity(self)

    def click(self):
        """处理点击事件"""
        if self._on_close:
            self._on_close()

    def mousePressEvent(self, event):
        """处理鼠标点击事件"""
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self.click()
