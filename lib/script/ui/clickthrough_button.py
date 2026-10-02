"""鼠标穿透按钮类"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QColor, QPainter

from config.config import UI, TIMEOUTS
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.voice.ams_clickthrough_reminder import AmsClickthroughReminderSound
from lib.core.anchor_utils import (
    animate_opacity,
    refresh_last_activity,
)
from lib.script.ui.render_bridge import family_placement, move_widget_to_global, ui_font as get_ui_font, widget_global_rect
from lib.script.ui.rect_action_button_style import paint_rect_action_button
from lib.core.render.backends.qt.widgets.anchors import get_anchor_point as resolve_anchor_point


def _hex(color: QColor) -> str:
    return color.name()


class ClickThroughButton(QWidget):
    """
    鼠标穿透按钮，与输入框风格一致，对齐到输入框左下角下方。
    当输入框显示时显示，输入框隐藏时隐藏。
    """

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, on_click=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._on_click = on_click
        self._visible = False
        self._clickthrough_enabled = False  # 鼠标穿透状态
        self._description = TOOLTIPS['clickthrough_button']
        self._hovered = False

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'clickthrough_button'

        # 帧事件用于位置刷新；按钮族落位统一由 RightClickUiLayer 的锚点图解算，
        # 本控件不再收发 UI_ANCHOR_RESPONSE / UI_CREATE 锚点事件（档位 2）。
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 字体设置
        self._font = get_ui_font()
        self._font.setBold(True)

        # 空闲超时自动关闭功能（与 command_dialog 共享超时时间）
        self._idle_timeout = TIMEOUTS['idle_close_ms']  # 10秒无操作自动关闭
        self._last_activity_time = 0
        self._clickthrough_reminder_sound = AmsClickthroughReminderSound(
            interruptible=False
        )

        # 订阅鼠标事件以重置空闲计时器
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._reset_idle_timer)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._reset_idle_timer)

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

    def paintEvent(self, event):
        """绘制2px黑色边框、2px青色边框、粉色背景和居中的文字"""
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, '鼠标穿透', hovered=self._hovered)

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if self._visible:
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 由 RightClickUiLayer 的锚点图给出整族矩形。"""
        rect = family_placement(self, 'clickthrough')
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

    def _reset_idle_timer(self, event=None):
        """重置空闲计时器"""
        refresh_last_activity(self)

    def _animate(self, target: float):
        animate_opacity(self._anim, self._opacity, target)

    def click(self):
        """处理点击事件 - 始终启用鼠标穿透模式"""
        self._clickthrough_enabled = True
        self._clickthrough_reminder_sound.play()

        # 发布鼠标穿透模式开启事件
        toggle_event = Event(EventType.UI_CLICKTHROUGH_TOGGLE, {
            'enabled': True
        })
        self._event_center.publish(toggle_event)

        # 发布信息气泡事件
        info_event = Event(EventType.INFORMATION, {
            'text': '鼠标穿透已开启',
            'min': 0,    # 最小显示 0 tick
            'max': 60    # 最大显示 60 tick
        })
        self._event_center.publish(info_event)

        # 获取PetWindow实例并传递给toggle方法
        # 通过事件中心获取PetWindow实例
        # 发布命令框关闭事件（如果命令框正在显示）
        from lib.core.event.center import get_event_center
        event_center = get_event_center()

        # 发布一个特殊事件来通知PetWindow关闭命令框
        close_cmd_event = Event(EventType.UI_COMMAND_TOGGLE, {
            'entity': None  # 传递None表示直接关闭
        })
        event_center.publish(close_cmd_event)

        # 重绘按钮以更新文本显示
        self.update()

        # 如果有回调函数，也调用它
        if self._on_click:
            self._on_click(True)

    def mousePressEvent(self, event):
        """处理鼠标点击事件"""
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self.click()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)
