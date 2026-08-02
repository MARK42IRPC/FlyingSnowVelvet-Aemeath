"""恢复穿透按钮类"""
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QColor

from config.config import UI
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.screen_utils import clamp_rect_position
from lib.core.qt_bridge.widget_anchors import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
)
from lib.core.qt_bridge.window import coerce_qpoint
from lib.script.ui.rect_action_button_style import RectActionButton


def _hex(color: QColor) -> str:
    return color.name()


class RestoreButton(RectActionButton):
    """
    恢复穿透按钮，在穿透模式下根据鼠标距离动态调整透明度，对齐到主窗口下中锚点。
    鼠标靠近时逐渐显示，远离时逐渐透明，按钮始终存在。
    """

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, pet_widget=None):
        super().__init__(self.WIDTH, self.HEIGHT, TOOLTIPS['restore_button'])

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'restore_button'

        # 锚点配置：对齐到 pet_window 的下中锚点
        self._target_ui_id = 'pet_window'
        self._target_anchor_id = 'bottom'
        self._self_anchor_id = 'top'  # 使用上中锚点对齐

        # 位置偏移：往下偏移 4 像素
        self._offset_x = 0
        self._offset_y = scale_px(4, min_abs=1)

        # 鼠标靠近阈值（像素）
        self._proximity_threshold = scale_px(100, min_abs=1)
        # 渐变距离：超出阈值后逐渐透明的距离
        self._fade_distance = scale_px(50, min_abs=1)

        # 鼠标穿透模式标志
        self._clickthrough_enabled = False

        # 鼠标位置
        self._mouse_pos = None

        # 当前透明度
        self._current_opacity = 1.0
        self._target_opacity = 1.0

        # 动画进行中标志
        self._animating = False

        # 当前锚点位置
        self._anchor_point = None

        # 锚点是否可用
        self._anchor_available = True

        # 如果提供了 pet_widget，直接计算初始锚点位置
        if pet_widget is not None:
            from config.config import ANIMATION
            pet_width = ANIMATION['pet_size'][0]
            pet_height = ANIMATION['pet_size'][1]
            pet_pos = pet_widget.get_core_position()

            # 计算主窗口的 bottom 锚点位置
            self._anchor_point = QPoint(
                int(pet_pos.x) + pet_width // 2,
                int(pet_pos.y) + pet_height
            )

            # 直接更新位置
            self._update_position()

            # 发布 UI 创建请求（用于后续更新）
            create_event = Event(EventType.UI_CREATE, {
                'window_id': self._target_ui_id,
                'anchor_id': self._target_anchor_id,
                'ui_id': self._ui_id
            })
            self._event_center.publish(create_event)

        # 按钮始终显示，通过透明度控制可见性
        self.fade_in()

        # 订阅帧事件用于位置刷新和鼠标距离检测
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 订阅鼠标移动事件
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._on_mouse_move)

        # 订阅锚点响应事件
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅 UI 创建事件，返回自己的坐标
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

        # 订阅鼠标穿透模式切换事件
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
        return '恢复穿透'

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置和根据鼠标距离调整透明度"""
        # 刷新位置（始终刷新）
        if self._anchor_available and self._anchor_point:
            self._update_position()

        # 动画进行中时不设置透明度，避免冲突
        if self._animating:
            return

        # 鼠标距离检测 - 靠近时alpha提升，远离时alpha降低
        if self._anchor_point is None:
            # 锚点未初始化，完全显示且可交互
            self._target_opacity = 1.0
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        elif self._mouse_pos is None:
            # 鼠标位置未获取，完全显示且可交互
            self._target_opacity = 1.0
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        else:
            # 计算鼠标与锚点的距离
            distance = ((self._mouse_pos.x() - self._anchor_point.x()) ** 2 +
                       (self._mouse_pos.y() - self._anchor_point.y()) ** 2) ** 0.5

            # 根据距离计算目标透明度
            if distance <= self._proximity_threshold:
                # 在阈值内，完全显示且可交互
                self._target_opacity = 1.0
                self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            elif distance <= self._proximity_threshold + self._fade_distance:
                # 在渐变范围内，逐渐透明但仍可交互
                fade_progress = (distance - self._proximity_threshold) / self._fade_distance
                self._target_opacity = 1.0 - fade_progress
                self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                # 超出渐变范围，半透明显示且鼠标穿透
                self._target_opacity = 0.3
                self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # 直接设置透明度
        self.set_direct_opacity(self._target_opacity)

    def _on_mouse_move(self, event):
        """处理鼠标移动事件 - 追踪鼠标位置"""
        self._mouse_pos = coerce_qpoint(event.data.get('global_pos'))

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')
        new_anchor_point = coerce_qpoint(event.data.get('anchor_point'))

        # 只处理来自 pet_window 的锚点响应事件
        if window_id != self._target_ui_id:
            return

        # 如果锚点不可用，不处理锚点更新
        if not self._anchor_available:
            return

        # 处理两种情况：
        # 1. 专门针对此 UI 组件的锚点响应（来自 pet_window）
        # 2. pet_window 移动时的全局锚点更新（ui_id='all'）
        if ui_id == self._ui_id:
            # 专门针对此 UI 组件的锚点响应
            # event.data.get('anchor_point') 已经是 pet_window bottom 锚点的全局坐标
            # 直接使用，不需要再计算
            # 只在锚点位置改变时更新
            if self._anchor_point != new_anchor_point:
                self._anchor_point = new_anchor_point
                self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id:
            # pet_window 移动时的全局锚点更新
            # 需要根据当前锚点 ID 计算新的锚点位置
            if anchor_id == 'all':
                # pet_window 的新位置（左上角坐标）
                pet_pos = coerce_qpoint(event.data.get('anchor_point'))
                if pet_pos is None:
                    return
                # 获取 pet_window 的尺寸来计算 bottom 锚点
                from config.config import ANIMATION
                pet_width = ANIMATION['pet_size'][0]
                pet_height = ANIMATION['pet_size'][1]
                # 计算 bottom 锚点位置
                new_anchor_point = QPoint(
                    pet_pos.x() + pet_width // 2,  # bottom 锚点的 X 坐标
                    pet_pos.y() + pet_height  # bottom 锚点的 Y 坐标
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

    def _on_clickthrough_toggle(self, event):
        """处理鼠标穿透模式切换事件"""
        enabled = event.data.get('enabled', False)
        self._clickthrough_enabled = enabled

        if enabled:
            # 穿透模式启用，重置锚点
            self._anchor_point = None

    def _update_position(self):
        """更新窗口位置 - 上中锚点对齐到 pet_window 的下中锚点"""
        if not self._anchor_point:
            return

        # self._anchor_point 是全局坐标（pet_window bottom 锚点的全局坐标）
        # bottom 锚点的位置：(pet_x + pet_width // 2, pet_y + pet_height)
        # RestoreButton 是独立窗口，使用全局坐标

        # 计算新的窗口位置
        # 我们要让自己的 top 锚点对齐到 pet_window 的 bottom 锚点
        # self._anchor_point.x() 已经是 pet_window 下中锚点的全局 X 坐标
        # self._anchor_point.y() 已经是 pet_window 下中锚点的全局 Y 坐标
        # 按钮的 top 相对于按钮左上角的坐标是 (width // 2, 0)
        # 所以按钮的左上角应该在：(锚点.x() - width // 2, 锚点.y())

        # X 轴：pet_window 下中锚点 X 坐标 - 按钮宽度一半 + 偏移量
        new_x = self._anchor_point.x() - self.WIDTH // 2 + self._offset_x

        # Y 轴：pet_window 下中锚点 Y 坐标 + 偏移量
        new_y = self._anchor_point.y() + self._offset_y

        # 边界检查（多屏：按锚点所在屏幕裁剪）
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=self._anchor_point,
            fallback_widget=self,
        )

        self.move(x, y)

    def click(self):
        """处理点击事件 - 取消鼠标穿透并淡出"""
        # 发布关闭穿透模式事件
        toggle_event = Event(EventType.UI_CLICKTHROUGH_TOGGLE, {
            'enabled': False
        })
        self._event_center.publish(toggle_event)

        # 发布信息气泡事件
        info_event = Event(EventType.INFORMATION, {
            'text': '鼠标穿透已关闭',
            'min': 0,    # 最小显示 0 tick
            'max': 60    # 最大显示 60 tick
        })
        self._event_center.publish(info_event)

        # 淡出按钮
        self.fade_out()

    def mousePressEvent(self, event):
        """处理鼠标点击事件"""
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self.click()

    def fade_in(self):
        """淡入按钮"""
        # 显示窗口
        self.show()

        # 启动淡入动画
        self._animate(1.0)

    def fade_out(self):
        """淡出按钮"""
        # 在隐藏之前保存几何位置
        rect = self.geometry()

        # 发布粒子申请事件（使用保存的位置）
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        })
        self._event_center.publish(particle_event)

        # 启动淡出动画，完成后隐藏窗口
        self._animate(0.0, on_finished=self.hide)

    def _animate(self, target: float, on_finished=None):
        """执行淡入淡出动画"""
        super()._animate(target)
        self._animating = True

        # 动画完成回调
        def on_anim_finished():
            self._animating = False
            self._anim.finished.disconnect(on_anim_finished)
            if on_finished:
                on_finished()

        self._anim.finished.connect(on_anim_finished)
