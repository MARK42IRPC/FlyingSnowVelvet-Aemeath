"""缩放按钮类 - 放大/缩小桌宠"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QPainter

from config.config import UI, TIMEOUTS
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.desktop_actions import adjust_desktop_scale
from lib.core.render.layers import get_layer_manager, WindowLayer
from config.user_scale_config import get_user_scale_config
from lib.core.anchor_utils import (
    animate_opacity,
    refresh_last_activity,
)
from lib.script.ui.rect_action_button_style import paint_rect_action_button
from lib.script.ui.render_bridge import family_placement, move_widget_to_global, ui_font as get_ui_font, widget_global_rect
from lib.core.render.backends.qt.widgets.anchors import get_anchor_point as resolve_anchor_point


class ScaleUpButton(QWidget):
    """
    放大按钮，左锚点对齐到鼠标穿透按钮的右锚点。
    """

    WIDTH = scale_px(40, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)
    SCALE_DELTA = 0.1  # 每次点击调整的缩放量

    def __init__(self, clickthrough_button=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, WindowLayer.PET_UI)

        self._clickthrough_button = clickthrough_button
        self._scale_config = get_user_scale_config()

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._visible = False
        self._description = TOOLTIPS.get('scale_up_button', '放大桌宠（重启生效）')
        self._hovered = False

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'scale_up_button'

        # 帧事件用于位置刷新；按钮族落位统一由 RightClickUiLayer 的锚点图解算（档位 2）。
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 字体设置
        self._font = get_ui_font()
        self._font.setBold(True)

        # 空闲超时自动关闭功能
        self._idle_timeout = TIMEOUTS['idle_close_ms']
        self._last_activity_time = 0

        # 订阅鼠标事件以重置空闲计时器
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._reset_idle_timer)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._reset_idle_timer)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def get_anchor_point(self, anchor_id: str) -> QPoint:
        """获取指定锚点的位置（相对于窗口的坐标）"""
        return resolve_anchor_point(self, anchor_id)

    def paintEvent(self, event):
        """绘制2px黑色边框、2px青色边框、粉色背景和居中的文字"""
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, '+', hovered=self._hovered)

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if self._visible:
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 由 RightClickUiLayer 的锚点图给出整族矩形。"""
        rect = family_placement(self, 'scale_up')
        if rect is None:
            return
        move_widget_to_global(self, int(rect.x), int(rect.y))

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = widget_global_rect(self)
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (int(rect.x), int(rect.y), int(rect.x) + int(rect.width), int(rect.y) + int(rect.height))
        })
        self._event_center.publish(particle_event)

    def _on_fade_out_complete(self):
        """淡出动画完成时的回调"""
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        self.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _reset_idle_timer(self, event=None):
        """重置空闲计时器"""
        refresh_last_activity(self)

    def _animate(self, target: float):
        animate_opacity(self._anim, self._opacity, target)

    def click(self):
        """处理点击事件 - 放大桌宠"""
        adjust_desktop_scale(self.SCALE_DELTA)

        self.update()

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


class ScaleDownButton(QWidget):
    """
    缩小按钮，左锚点对齐到放大按钮的右锚点。
    """

    WIDTH = scale_px(40, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)
    SCALE_DELTA = -0.1  # 每次点击调整的缩放量

    def __init__(self, scale_up_button=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, WindowLayer.PET_UI)

        self._scale_up_button = scale_up_button
        self._scale_config = get_user_scale_config()

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._visible = False
        self._description = TOOLTIPS.get('scale_down_button', '缩小桌宠（重启生效）')
        self._hovered = False

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'scale_down_button'

        # 帧事件用于位置刷新；按钮族落位统一由 RightClickUiLayer 的锚点图解算（档位 2）。
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 字体设置
        self._font = get_ui_font()
        self._font.setBold(True)

        # 空闲超时自动关闭功能
        self._idle_timeout = TIMEOUTS['idle_close_ms']
        self._last_activity_time = 0

        # 订阅鼠标事件以重置空闲计时器
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._reset_idle_timer)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._reset_idle_timer)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def get_anchor_point(self, anchor_id: str) -> QPoint:
        """获取指定锚点的位置（相对于窗口的坐标）"""
        return resolve_anchor_point(self, anchor_id)

    def paintEvent(self, event):
        """绘制2px黑色边框、2px青色边框、粉色背景和居中的文字"""
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, '-', hovered=self._hovered)

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if self._visible:
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 由 RightClickUiLayer 的锚点图给出整族矩形。"""
        rect = family_placement(self, 'scale_down')
        if rect is None:
            return
        move_widget_to_global(self, int(rect.x), int(rect.y))

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        self._animate(1.0)
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = widget_global_rect(self)
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (int(rect.x), int(rect.y), int(rect.x) + int(rect.width), int(rect.y) + int(rect.height))
        })
        self._event_center.publish(particle_event)

    def _on_fade_out_complete(self):
        """淡出动画完成时的回调"""
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        self.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _reset_idle_timer(self, event=None):
        """重置空闲计时器"""
        refresh_last_activity(self)

    def _animate(self, target: float):
        animate_opacity(self._anim, self._opacity, target)

    def click(self):
        """处理点击事件 - 放大桌宠"""
        adjust_desktop_scale(self.SCALE_DELTA)

        self.update()

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
