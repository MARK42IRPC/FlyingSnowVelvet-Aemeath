"""缩放按钮类 - 放大/缩小桌宠"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QPainter

from config.config import COLORS, UI, TIMEOUTS
from config.font_config import get_ui_font
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from config.user_scale_config import get_user_scale_config
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
    animate_opacity,
    refresh_last_activity,
)
from lib.script.ui.rect_action_button_style import paint_rect_action_button


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
        get_topmost_manager().register(self)

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
        self._target_ui_id = 'clickthrough_button'

        # 订阅帧事件用于位置刷新
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 订阅锚点响应事件（用于即时跟随）
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅 UI 创建事件
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

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

    def _on_ui_create(self, event):
        """UI ?????? - ???????"""
        target_ui_id = event.data.get('ui_id')
        request_anchor_id = event.data.get('anchor_id')

        if target_ui_id == self._ui_id:
            publish_widget_anchor_response(
                self._event_center,
                self,
                window_id=self._ui_id,
                anchor_id=request_anchor_id,
                ui_id=target_ui_id,
            )

    def _on_anchor_response(self, event):
        """锚点响应事件处理 - 上游按钮移动时立即跟随"""
        if not self._visible:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 的锚点响应（fade_in 时主动请求）
        # 2. 上游 clickthrough_button 移动时的全局锚点更新
        if ui_id == self._ui_id and window_id == self._target_ui_id:
            self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id and anchor_id == 'all':
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 左锚点对齐到 clickthrough_button 的右锚点"""
        if not self._clickthrough_button:
            return

        # 获取 clickthrough_button 的位置和尺寸
        btn_x = self._clickthrough_button.x()
        btn_y = self._clickthrough_button.y()
        btn_width = self._clickthrough_button.width()
        btn_height = self._clickthrough_button.height()

        # clickthrough_button 的 right 锚点（全局坐标）
        # right 锚点 = (btn_x + btn_width, btn_y + btn_height // 2)
        target_right_x = btn_x + btn_width
        target_right_y = btn_y + btn_height // 2

        # 自己的 left 锚点 = (x, y + height // 2)
        # 我们要让自己的 left 锚点对齐到目标按钮的 right 锚点
        # 所以: x = target_right_x
        #      y + height // 2 = target_right_y  =>  y = target_right_y - height // 2

        new_x = target_right_x
        new_y = target_right_y - self.HEIGHT // 2

        # 边界检查（多屏：按目标按钮所在屏幕裁剪）
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=QPoint(target_right_x, target_right_y),
            fallback_widget=self,
        )

        if self.x() != x or self.y() != y:
            self.move(x, y)
            # 广播自身位置变化，供下游 UI（缩小按钮）即时跟随
            anchor_update_event = Event(EventType.UI_ANCHOR_RESPONSE, {
                'window_id': self._ui_id,
                'anchor_id': 'all',
                'anchor_point': QPoint(x, y),
                'ui_id': 'all'
            })
            self._event_center.publish(anchor_update_event)

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        create_event = Event(EventType.UI_CREATE, {
            'window_id': self._target_ui_id,
            'anchor_id': 'right',
            'ui_id': self._ui_id
        })
        self._event_center.publish(create_event)
        self._animate(1.0)
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = self.geometry()
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
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
        new_scale = self._scale_config.adjust_scale(self.SCALE_DELTA)

        # 发布信息气泡事件
        info_event = Event(EventType.INFORMATION, {
            'text': f'缩放: {new_scale:.1f}（重启生效）',
            'min': 0,
            'max': 60
        })
        self._event_center.publish(info_event)

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
        get_topmost_manager().register(self)

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
        self._target_ui_id = 'scale_up_button'

        # 订阅帧事件用于位置刷新
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 订阅锚点响应事件（用于即时跟随）
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅 UI 创建事件
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

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

    def _on_ui_create(self, event):
        """UI ?????? - ???????"""
        target_ui_id = event.data.get('ui_id')
        request_anchor_id = event.data.get('anchor_id')

        if target_ui_id == self._ui_id:
            publish_widget_anchor_response(
                self._event_center,
                self,
                window_id=self._ui_id,
                anchor_id=request_anchor_id,
                ui_id=target_ui_id,
            )

    def _on_anchor_response(self, event):
        """锚点响应事件处理 - 上游按钮移动时立即跟随"""
        if not self._visible:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 的锚点响应（fade_in 时主动请求）
        # 2. 上游 scale_up_button 移动时的全局锚点更新
        if ui_id == self._ui_id and window_id == self._target_ui_id:
            self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id and anchor_id == 'all':
            self._update_position()

    def _update_position(self):
        """更新窗口位置 - 左锚点对齐到 scale_up_button 的右锚点"""
        if not self._scale_up_button:
            return

        # 获取 scale_up_button 的位置和尺寸
        btn_x = self._scale_up_button.x()
        btn_y = self._scale_up_button.y()
        btn_width = self._scale_up_button.width()
        btn_height = self._scale_up_button.height()

        # scale_up_button 的 right 锚点（全局坐标）
        target_right_x = btn_x + btn_width
        target_right_y = btn_y + btn_height // 2

        # 自己的 left 锚点对齐到目标按钮的 right 锚点
        new_x = target_right_x
        new_y = target_right_y - self.HEIGHT // 2

        # 边界检查（多屏：按目标按钮所在屏幕裁剪）
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=QPoint(target_right_x, target_right_y),
            fallback_widget=self,
        )

        if self.x() != x or self.y() != y:
            self.move(x, y)

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        create_event = Event(EventType.UI_CREATE, {
            'window_id': self._target_ui_id,
            'anchor_id': 'right',
            'ui_id': self._ui_id
        })
        self._event_center.publish(create_event)
        self._animate(1.0)
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = self.geometry()
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
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
        """处理点击事件 - 缩小桌宠"""
        new_scale = self._scale_config.adjust_scale(self.SCALE_DELTA)

        # 发布信息气泡事件
        info_event = Event(EventType.INFORMATION, {
            'text': f'缩放: {new_scale:.1f}（重启生效）',
            'min': 0,
            'max': 60
        })
        self._event_center.publish(info_event)

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
