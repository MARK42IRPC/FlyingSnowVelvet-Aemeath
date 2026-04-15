"""鼠标穿透按钮类"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QColor, QFont, QPainter

from config.config import COLORS, UI, FONT, TIMEOUTS
from config.font_config import get_ui_font
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.voice.ams_clickthrough_reminder import AmsClickthroughReminderSound
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
    animate_opacity,
    refresh_last_activity,
)


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
        get_topmost_manager().register(self)

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

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'clickthrough_button'

        # 锚点配置：对齐到 command_dialog 的左上锚点
        self._target_ui_id = 'command_dialog'
        self._target_anchor_id = 'top_left'
        self._self_anchor_id = 'bottom_left'  # 使用左下锚点对齐

        # 位置偏移：往上偏移 2 像素
        self._offset_x = 0
        self._offset_y = scale_px(-2, min_abs=1)

        # 订阅帧事件用于位置刷新
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 订阅锚点响应事件
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅 UI 创建事件，返回自己的坐标
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

        # 当前锚点位置
        self._anchor_point = None

        # 锚点是否可用（当锚点物体消失时设为False）
        self._anchor_available = False

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
        painter.setRenderHint(QPainter.Antialiasing, False)
        layer = scale_px(2, min_abs=1)
        content_inset = layer * 2

        # 绘制2px黑色边框（最外层）
        painter.fillRect(self.rect(), COLORS['black'])

        # 绘制2px青色边框（中间层）
        cyan_rect = self.rect().adjusted(layer, layer, -layer, -layer)
        painter.fillRect(cyan_rect, COLORS['cyan'])

        # 绘制粉色背景（最内层）
        content_rect = self.rect().adjusted(
            content_inset, content_inset, -content_inset, -content_inset
        )
        painter.fillRect(content_rect, COLORS['pink'])

        # 绘制居中的"鼠标穿透"粗体文字
        painter.setPen(COLORS['black'])
        painter.setFont(self._font)
        painter.drawText(content_rect, Qt.AlignCenter, '鼠标穿透')

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if self._visible and self._anchor_available and self._anchor_point:
            # 只有在锚点可用时才跟随
            self._update_position()

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        # 如果锚点不可用，不处理锚点更新
        if not self._anchor_available:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 组件的锚点响应（来自 command_dialog）
        # 2. command_dialog 移动时的全局锚点更新（ui_id='all'）
        if ui_id == self._ui_id:
            # 专门针对此 UI 组件的锚点响应
            # event.data.get('anchor_point') 已经是 command_dialog top_left 锚点的全局坐标
            # 直接使用，不需要再计算
            new_anchor_point = event.data.get('anchor_point')
            # 只在锚点位置改变时更新
            if self._anchor_point != new_anchor_point:
                self._anchor_point = new_anchor_point
                self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id:
            # command_dialog 移动时的全局锚点更新
            # 需要根据当前锚点 ID 计算新的锚点位置
            if anchor_id == 'all':
                # command_dialog 的新位置（左上角坐标）
                cmd_pos = event.data.get('anchor_point')
                # 获取 command_dialog 的尺寸来计算 top_left 锚点
                from config.config import UI
                cmd_width = UI['cmd_window_width']
                cmd_height = UI['cmd_window_height']
                # 计算 top_left 锚点位置（就是左上角）
                new_anchor_point = QPoint(
                    cmd_pos.x(),  # top_left 锚点的 X 坐标
                    cmd_pos.y()  # top_left 锚点的 Y 坐标
                )
                # 只在锚点位置改变时更新
                if self._anchor_point != new_anchor_point:
                    self._anchor_point = new_anchor_point
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

    def _update_position(self):
        """更新窗口位置 - 左下锚点对齐到 command_dialog 的左上锚点"""
        if not self._anchor_point:
            return

        # self._anchor_point 是全局坐标（command_dialog top_left 锚点的全局坐标）
        # top_left 锚点的位置：(cmd_x, cmd_y)
        # ClickThroughButton 是独立窗口，使用全局坐标

        # 计算新的窗口位置
        # 我们要让自己的 bottom_left 锚点对齐到 command_dialog 的 top_left 锚点
        # self._anchor_point.x() 已经是 command_dialog 左上角的全局 X 坐标
        # self._anchor_point.y() 已经是 command_dialog 左上角的全局 Y 坐标
        # 按钮的 bottom_left 相对于按钮左上角的坐标是 (0, HEIGHT)
        # 所以按钮的左上角应该在：(锚点.x() - 0, 锚点.y() - HEIGHT)

        # X 轴：command_dialog 左上角 X 坐标 - 0 + 偏移量
        new_x = self._anchor_point.x() - 0 + self._offset_x

        # Y 轴：command_dialog 左上角 Y 坐标 - 按钮高度 + 偏移量
        new_y = self._anchor_point.y() - self.HEIGHT + self._offset_y

        # 边界检查（多屏：按锚点所在屏幕裁剪）
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=self._anchor_point,
            fallback_widget=self,
        )

        if self.x() != x or self.y() != y:
            self.move(x, y)
            # 广播自身位置变化，供下游 UI（如缩放按钮）即时跟随
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
        self._anchor_available = True  # 锚点可用

        # 直接显示窗口（位置会在 _on_anchor_response 中更新）
        self.show()

        # 发布 UI 创建请求（用于后续更新）
        create_event = Event(EventType.UI_CREATE, {
            'window_id': self._target_ui_id,
            'anchor_id': self._target_anchor_id,
            'ui_id': self._ui_id
        })
        self._event_center.publish(create_event)

        self._animate(1.0)

        # 重置空闲计时器
        self._reset_idle_timer()

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False
        self._anchor_available = False  # 锚点不可用，停止跟随

        # 在隐藏之前保存几何位置
        rect = self.geometry()

        # 设置动画完成后的回调
        self._anim.finished.connect(self._on_fade_out_complete)
        # 启动淡出动画
        self._animate(0.0)

        # 发布粒子申请事件（使用保存的位置）
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
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
