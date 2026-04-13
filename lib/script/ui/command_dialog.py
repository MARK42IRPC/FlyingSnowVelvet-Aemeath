"""CMD命令输入框类"""

from PyQt5.QtWidgets import QWidget, QLineEdit, QHBoxLayout, QApplication, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QEvent
from PyQt5.QtGui import QColor, QPainter, QCursor

from config.config import COLORS, UI, COMMAND_DIALOG, ANIMATION
from config.font_config import get_cmd_font
from config.scale import scale_px, scale_style_px
from config.tooltip_config import TOOLTIPS
from lib.script.ui.command_dialog_handler import CommandDialogEventHandler
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
    animate_opacity,
)


def _hex(color: QColor) -> str:
    return color.name()

_AUTO_HIDE_MOUSE_DISTANCE = UI.get('auto_hide_mouse_distance', scale_px(300, min_abs=1))


class CommandDialog(QWidget):
    """
    浮动的单行命令输入框，随宠物位置显示，右键切换显示/隐藏。
    使用事件系统获取锚点位置，由全局帧事件驱动位置刷新。
    """

    def __init__(self, on_command, bubble=None, close_button=None, clickthrough_button=None, hint_box=None, scale_up_button=None, scale_down_button=None, launch_wuwa_button=None, chat_mode_button=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(UI['cmd_window_width'], UI['cmd_window_height'])
        get_topmost_manager().register(self)

        self._on_command = on_command
        self._close_button = close_button  # 引用关闭按钮
        self._clickthrough_button = clickthrough_button  # 引用鼠标穿透按钮
        self._hint_box = hint_box  # 引用命令提示框
        self._scale_up_button = scale_up_button  # 引用放大按钮
        self._scale_down_button = scale_down_button  # 引用缩小按钮
        self._launch_wuwa_button = launch_wuwa_button  # 引用启动鸣潮按钮
        self._chat_mode_button = chat_mode_button  # 语音/文字模式切换按钮

        # 输入框
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText('cmd')
        self._entry.setFont(get_cmd_font())
        self._entry.setStyleSheet(scale_style_px(f"""
            QLineEdit {{
                background: white;
                color: black;
                border: 2px solid {_hex(COLORS['pink'])};
                padding: 2px 4px;
            }}
        """))
        self._entry._description = TOOLTIPS['command_dialog']

        # 布局（四周留4px给边框：2px黑边+2px青边）
        layout = QHBoxLayout(self)
        layout_pad = scale_px(4, min_abs=1)
        layout.setContentsMargins(layout_pad, layout_pad, layout_pad, layout_pad)
        layout.addWidget(self._entry)

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        # 动画结束后，若已设为隐藏状态则调用 hide()（仅连接一次，避免 lambda 累积）
        self._anim.finished.connect(self._on_anim_finished)

        self._visible = False
        self._description = TOOLTIPS['command_dialog']

        # 事件中心
        self._event_center = get_event_center()

        # UI 组件 ID
        self._ui_id = 'command_dialog'

        # 锚点配置
        self._anchor_id = 'right'  # 主窗口的右锚点
        self._self_anchor_id = 'left'  # 自己的左锚点

        # 位置偏移
        self._offset_x = scale_px(6)   # 向右偏移 6 像素
        self._offset_y = 0   # 垂直无偏移

        # 订阅帧事件用于位置刷新
        self._event_center.subscribe(EventType.FRAME, self._on_frame)

        # 订阅锚点响应事件
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)

        # 订阅 UI 创建事件，响应其他 UI 组件的锚点请求
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)

        # 订阅命令框切换事件（异步处理）
        self._event_center.subscribe(EventType.UI_COMMAND_TOGGLE, self._on_command_toggle)
        # 订阅提示框点击事件（默认提示项点击填充输入）
        self._event_center.subscribe(EventType.UI_HINT_PICK, self._on_hint_pick)

        # 输入框回车：解析并发布输入事件
        self._entry.returnPressed.connect(self._on_return_pressed)

        # 拦截特殊键（Tab / ↑↓ / ←→）以驱动提示框
        self._entry.installEventFilter(self)
        # 文本变化时实时刷新提示框
        if self._hint_box:
            self._entry.textChanged.connect(self._hint_box.update_input)

        # 当前锚点位置
        self._anchor_point = None
        self._pet_top_left = None
        # 当前关联的实体（用于CMD窗口定位）
        self._entity = None

        # 从配置文件读取位置偏移参数
        self._offset_x = COMMAND_DIALOG.get('offset_x', scale_px(6))
        self._offset_y = COMMAND_DIALOG.get('offset_y', scale_px(0))
        # 当前实际放置侧（仅记录用）；每次定位始终优先尝试右侧
        self._placement_side = 'right'

        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # 输入框焦点追踪：用于控制提示框显隐
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        # 创建事件处理器
        self._event_handler = CommandDialogEventHandler(self)

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

    @staticmethod
    def _get_pet_size() -> tuple[int, int]:
        """返回主宠窗口尺寸。"""
        width, height = ANIMATION['pet_size']
        return int(width), int(height)

    def _build_pet_anchor(self, pet_top_left: QPoint, anchor_id: str) -> QPoint:
        """根据主宠左上角坐标构造指定锚点的全局坐标。"""
        pet_w, pet_h = self._get_pet_size()
        anchor_map = {
            'top': QPoint(pet_top_left.x() + pet_w // 2, pet_top_left.y()),
            'bottom': QPoint(pet_top_left.x() + pet_w // 2, pet_top_left.y() + pet_h),
            'left': QPoint(pet_top_left.x(), pet_top_left.y() + pet_h // 2),
            'right': QPoint(pet_top_left.x() + pet_w, pet_top_left.y() + pet_h // 2),
            'top_left': QPoint(pet_top_left.x(), pet_top_left.y()),
            'top_right': QPoint(pet_top_left.x() + pet_w, pet_top_left.y()),
            'bottom_left': QPoint(pet_top_left.x(), pet_top_left.y() + pet_h),
            'bottom_right': QPoint(pet_top_left.x() + pet_w, pet_top_left.y() + pet_h),
            'center': QPoint(pet_top_left.x() + pet_w // 2, pet_top_left.y() + pet_h // 2),
        }
        return anchor_map.get(anchor_id, anchor_map['right'])

    def _anchor_to_pet_top_left(self, anchor_point: QPoint, anchor_id: str) -> QPoint:
        """根据主宠某个锚点的全局坐标反推其左上角坐标。"""
        pet_w, pet_h = self._get_pet_size()
        offset_map = {
            'top': QPoint(pet_w // 2, 0),
            'bottom': QPoint(pet_w // 2, pet_h),
            'left': QPoint(0, pet_h // 2),
            'right': QPoint(pet_w, pet_h // 2),
            'top_left': QPoint(0, 0),
            'top_right': QPoint(pet_w, 0),
            'bottom_left': QPoint(0, pet_h),
            'bottom_right': QPoint(pet_w, pet_h),
            'center': QPoint(pet_w // 2, pet_h // 2),
        }
        offset = offset_map.get(anchor_id, offset_map['right'])
        return QPoint(anchor_point.x() - offset.x(), anchor_point.y() - offset.y())

    def paintEvent(self, event):
        """绘制2px黑色边框、2px青色边框和粉色背景"""
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

    def _on_command_toggle(self, event: Event):
        """处理命令框切换事件（异步）"""
        entity = event.data.get('entity')
        self._entity = entity  # 保存实体引用，用于CMD窗口定位
        if entity:
            # 调用toggle方法
            self.toggle(entity)
        elif self._visible:
            # entity为None且命令框正在显示，直接关闭
            self.toggle(None)

    def toggle(self, pet_widget):
        """切换显示/隐藏"""
        if self._visible:
            # 关闭前清空输入框，避免残留内容在下次打开时出现
            self._entry.clear()
            # 淡出
            self._visible = False
            self._animate(0.0)
            # 同时隐藏关闭按钮
            if self._close_button:
                self._close_button.fade_out()
            # 同时隐藏鼠标穿透按钮
            if self._clickthrough_button:
                self._clickthrough_button.fade_out()
            # 同时隐藏缩放按钮
            if self._scale_down_button:
                self._scale_down_button.fade_out()
            if self._scale_up_button:
                self._scale_up_button.fade_out()
            if self._launch_wuwa_button:
                self._launch_wuwa_button.fade_out()
            if self._chat_mode_button:
                self._chat_mode_button.fade_out()
            # 同时隐藏命令提示框
            if self._hint_box:
                self._hint_box.fade_out()
            # 恢复计时器
            resume_event = Event(EventType.TIMER_RESUME, {
                'source': 'command_dialog',
            })
            self._event_center.publish(resume_event)

            # 发布粒子申请事件
            rect = self.geometry()
            particle_event = Event(EventType.PARTICLE_REQUEST, {
                'particle_id': 'right_fade',
                'area_type': 'rect',
                'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
            })
            self._event_center.publish(particle_event)
        else:
            # 如果pet_widget为None，只显示命令框但不处理锚点
            if pet_widget is None:
                # 显示窗口
                self.show()
                self._entry.setFocus()

                # 同时显示关闭按钮
                if self._close_button:
                    self._close_button.fade_in()
                # 同时显示鼠标穿透按钮
                if self._clickthrough_button:
                    self._clickthrough_button.fade_in()
                # 同时显示缩放按钮
                if self._scale_up_button:
                    self._scale_up_button.fade_in()
                if self._scale_down_button:
                    self._scale_down_button.fade_in()
                if self._launch_wuwa_button:
                    self._launch_wuwa_button.fade_in()
                if self._chat_mode_button:
                    self._chat_mode_button.fade_in()
                if self._hint_box:
                    self._hint_box.fade_in()

                # 淡入
                self._visible = True
                self._animate(1.0)
                # 暂停计时器
                pause_event = Event(EventType.TIMER_PAUSE, {
                    'source': 'command_dialog',
                })
                self._event_center.publish(pause_event)
            else:
                # 正常的显示逻辑
                # 直接计算锚点位置，避免等待事件响应
                pet_pos = pet_widget.get_position()
                self._pet_top_left = QPoint(pet_pos.x(), pet_pos.y())
                self._placement_side = 'right'
                self._anchor_point = self._build_pet_anchor(
                    self._pet_top_left,
                    self._placement_side,
                )

                # 直接更新位置
                self._update_position()

                # 显示窗口
                self.show()
                self._entry.setFocus()

                # 同时显示关闭按钮
                if self._close_button:
                    self._close_button.fade_in()
                # 同时显示鼠标穿透按钮
                if self._clickthrough_button:
                    self._clickthrough_button.fade_in()
                # 同时显示缩放按钮
                if self._scale_up_button:
                    self._scale_up_button.fade_in()
                if self._scale_down_button:
                    self._scale_down_button.fade_in()
                if self._launch_wuwa_button:
                    self._launch_wuwa_button.fade_in()
                if self._chat_mode_button:
                    self._chat_mode_button.fade_in()
                if self._hint_box:
                    self._hint_box.fade_in()

                # 发布 UI 创建请求（用于后续更新，不阻塞显示）
                create_event = Event(EventType.UI_CREATE, {
                    'window_id': 'pet_window',
                    'anchor_id': self._anchor_id,
                    'ui_id': self._ui_id
                })
                self._event_center.publish(create_event)

                # 淡入
                self._visible = True
                self._animate(1.0)
                # 暂停计时器
                pause_event = Event(EventType.TIMER_PAUSE, {
                    'source': 'command_dialog',
                })
                self._event_center.publish(pause_event)

    def _on_ui_create(self, event):
        """?? UI ??????????? UI ???????"""
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')
        ui_id = event.data.get('ui_id')

        if window_id == self._ui_id:
            publish_widget_anchor_response(
                self._event_center,
                self,
                window_id=window_id,
                anchor_id=anchor_id,
                ui_id=ui_id,
            )

    def _on_frame(self, event):
        """帧事件处理 - 刷新位置"""
        if not self._visible:
            return
        if self._is_mouse_far_from_family():
            self.toggle(None)
            return
        if self._anchor_point:
            # 直接使用当前锚点位置更新窗口位置
            # 不需要每帧都请求新的锚点，因为锚点位置会在 _on_anchor_response 中更新
            self._update_position()

    def _on_hint_pick(self, event: Event):
        """处理命令提示框点击：填充输入并聚焦。"""
        text = event.data.get('text')
        if text is None:
            return
        if not self._visible:
            self.toggle(None)
        self._entry.setFocus()
        self._entry.setText(str(text))
        self._entry.setCursorPosition(len(self._entry.text()))

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 组件的锚点响应
        # 2. PetWindow 移动时的全局锚点更新（ui_id='all'）
        if ui_id == self._ui_id:
            # 专门针对此 UI 组件的锚点响应
            # event.data.get('anchor_point') 是 PetWindow 某锚点的全局坐标
            new_anchor_point = event.data.get('anchor_point')
            if new_anchor_point:
                self._pet_top_left = self._anchor_to_pet_top_left(new_anchor_point, anchor_id)
                self._anchor_point = self._build_pet_anchor(self._pet_top_left, self._placement_side)
                self._update_position()
        elif ui_id == 'all' and window_id == 'pet_window':
            # PetWindow 移动时的全局锚点更新
            # 需要根据当前锚点 ID 计算新的锚点位置
            if anchor_id == 'all':
                # PetWindow 的新位置（左上角坐标）
                pet_pos = event.data.get('anchor_point')
                if pet_pos:
                    self._pet_top_left = QPoint(pet_pos.x(), pet_pos.y())
                    self._anchor_point = self._build_pet_anchor(self._pet_top_left, self._placement_side)
                    self._update_position()

    def _update_position(self):
        """更新窗口位置：默认右侧显示，被边缘阻挡时左右翻转。"""
        if self._pet_top_left is None:
            if self._anchor_point is None:
                return
            self._pet_top_left = self._anchor_to_pet_top_left(self._anchor_point, self._anchor_id)

        pet_w, pet_h = self._get_pet_size()
        pet_pos = self._pet_top_left
        pet_center = QPoint(pet_pos.x() + pet_w // 2, pet_pos.y() + pet_h // 2)

        # Y 轴始终与主宠物垂直居中
        new_y = pet_center.y() - self.height() // 2 + self._offset_y

        def _candidate_x(side: str) -> int:
            if side == 'left':
                # 对话框放在主宠物左侧，保持同样间距
                return pet_pos.x() - self.width() - self._offset_x
            # 对话框放在主宠物右侧
            return pet_pos.x() + pet_w + self._offset_x

        # 始终优先尝试右侧；右侧被边缘阻挡时才回退到左侧
        current_side = 'right'
        candidate_x = _candidate_x(current_side)

        # 先尝试当前侧；如果被边缘裁剪，再尝试翻转到另一侧
        x, y, _ = clamp_rect_position(
            candidate_x,
            new_y,
            self.width(),
            self.height(),
            point=pet_center,
            fallback_widget=self,
        )

        if x != candidate_x:
            flipped_side = 'left' if current_side == 'right' else 'right'
            flipped_x = _candidate_x(flipped_side)
            fx, fy, _ = clamp_rect_position(
                flipped_x,
                new_y,
                self.width(),
                self.height(),
                point=pet_center,
                fallback_widget=self,
            )
            # 翻转后更贴近原始目标（或无需裁剪）时，采用翻转侧
            if fx == flipped_x or abs(fx - flipped_x) < abs(x - candidate_x):
                current_side = flipped_side
                x, y = fx, fy

        self._placement_side = current_side
        self._anchor_id = current_side
        self._anchor_point = self._build_pet_anchor(self._pet_top_left, self._placement_side)

        if self.x() != x or self.y() != y:
            self.move(x, y)

        # 发布锚点更新事件，通知 close_button 更新位置
        anchor_update_event = Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': self._ui_id,
            'anchor_id': 'all',
            'anchor_point': QPoint(x, y),
            'ui_id': 'all'
        })
        self._event_center.publish(anchor_update_event)

    def eventFilter(self, obj, event):
        """
        拦截输入框的特殊按键，驱动提示框的导航与补全。

        - Tab     → 用选中命令补全输入框
        - ↑ ↓    → 上下切换选中行
        - ←（游标在行首）→ 上一页
        - →（游标在行尾）→ 下一页
        """
        if obj is self._entry and event.type() == QEvent.KeyPress and self._hint_box:
            key = event.key()
            if key == Qt.Key_Tab:
                completion = self._hint_box.get_completion()
                if completion:
                    self._entry.setText(completion)
                    self._entry.setCursorPosition(len(completion))
                return True  # 消耗事件，不切换焦点
            elif key == Qt.Key_Up:
                self._hint_box.navigate(-1)
                return True
            elif key == Qt.Key_Down:
                self._hint_box.navigate(1)
                return True
            elif key == Qt.Key_Left and self._entry.cursorPosition() == 0:
                self._hint_box.turn_page(-1)
                return True
            elif key == Qt.Key_Right and self._entry.cursorPosition() == len(self._entry.text()):
                self._hint_box.turn_page(1)
                return True
        return super().eventFilter(obj, event)

    def _animate(self, target: float):
        """执行淡入淡出动画"""
        animate_opacity(self._anim, self._opacity, target)

    def _on_anim_finished(self):
        """动画结束回调 - 淡出完成时隐藏窗口（仅在不可见状态时执行）"""
        if not self._visible:
            self.hide()

    def _on_return_pressed(self):
        """输入框回车处理：关闭对话框并发布对应输入事件"""
        raw = self._entry.text().strip()
        if not raw:
            return

        # # 后无显式参数（无空格）时，使用提示框当前高亮项以默认参数执行
        # 必须在 clear() 之前读取，否则 clear() 触发 textChanged 会重置 hint_box 状态
        if raw.startswith('#') and ' ' not in raw[1:] and self._hint_box:
            completion = self._hint_box.get_completion()  # 格式: "#name "
            if completion:
                raw = completion.strip()  # → "#name"

        # 1. 清空输入框
        self._entry.clear()

        # 2. 关闭对话框（淡出 + right_fade 粒子），仅在可见时触发
        if self._visible:
            self.toggle(None)

        # 3. 按前缀发布输入事件
        if raw.startswith('/'):
            # 打开CMD窗口并执行命令
            event = Event(EventType.UI_OPEN_CMD_WINDOW_WITH_COMMAND, {
                'command': raw[1:].strip(),
                'entity': self._entity,
                'raw': raw
            })
        elif raw.startswith('#'):
            text = raw[1:].strip()
            if not text:
                return  # 无命令名且提示框无选中项，忽略
            event = Event(EventType.INPUT_HASH, {'text': text, 'raw': raw})
        else:
            event = Event(EventType.INPUT_CHAT, {'text': raw, 'raw': raw})

        self._event_center.publish(event)

    def _execute(self):
        """解析输入并发布对应输入事件"""
        raw = self._entry.text().strip()
        if not raw:
            return
        self._entry.clear()

        if raw.startswith('/'):
            # 打开CMD窗口并执行命令
            event = Event(EventType.UI_OPEN_CMD_WINDOW_WITH_COMMAND, {
                'command': raw[1:].strip(),
                'entity': self._entity,
                'raw': raw
            })
        elif raw.startswith('#'):
            text = raw[1:].strip()
            event = Event(EventType.INPUT_HASH, {'text': text, 'raw': raw})
        else:
            event = Event(EventType.INPUT_CHAT, {'text': raw, 'raw': raw})

        self._event_center.publish(event)

    def _on_focus_changed(self, old_widget, new_widget):
        """
        输入框焦点变化回调（由 QApplication.focusChanged 触发）。

        获得焦点：显示提示框。
        失去焦点：不做关闭处理，交由鼠标距离守卫统一决定。
        """
        if new_widget is self._entry:
            if self._hint_box:
                self._hint_box.fade_in()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _iter_family_widgets(self):
        widgets = [
            self,
            self._entry,
            self._hint_box,
            self._close_button,
            self._clickthrough_button,
            self._scale_up_button,
            self._scale_down_button,
            self._launch_wuwa_button,
            self._chat_mode_button,
        ]
        if self._hint_box is not None:
            widgets.append(getattr(self._hint_box, '_prev_btn', None))
            widgets.append(getattr(self._hint_box, '_next_btn', None))
        return [w for w in widgets if w is not None]

    def _is_mouse_far_from_family(self) -> bool:
        mouse = QCursor.pos()
        limit_sq = _AUTO_HIDE_MOUSE_DISTANCE * _AUTO_HIDE_MOUSE_DISTANCE
        nearest_sq = None
        for widget in self._iter_family_widgets():
            try:
                if not widget.isVisible():
                    continue
                center = widget.geometry().center()
                dx = mouse.x() - center.x()
                dy = mouse.y() - center.y()
                dist_sq = dx * dx + dy * dy
                if nearest_sq is None or dist_sq < nearest_sq:
                    nearest_sq = dist_sq
            except RuntimeError:
                continue
        if nearest_sq is None:
            return False
        return nearest_sq > limit_sq
