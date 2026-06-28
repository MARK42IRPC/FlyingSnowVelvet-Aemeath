"""气泡框类"""
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect, QApplication
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QRect
from PyQt5.QtGui import QPainter, QFontMetrics, QCursor

from config.config import COLORS, UI, BUBBLE_CONFIG
from config.font_config import (
    get_ui_font,
    get_digit_font,
    draw_mixed_text,
    wrap_mixed_text,
    measure_mixed_text,
)
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager
from lib.core.logger import get_logger
from lib.core.screen_utils import clamp_rect_position, get_screen_geometry_for_point
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
    apply_ui_opacity,
)
from lib.core.voice.ams_bug import AmsBugSound

_logger = get_logger(__name__)


class BubbleInfo:
    """气泡信息"""
    def __init__(self, text: str, min_ticks: int, max_ticks: int, align: str = 'center'):
        self.text = text
        self.min_ticks = min_ticks
        self.max_ticks = max_ticks
        self.elapsed_ticks = 0
        self.align = align  # 'left' | 'center'


class Bubble(QWidget):
    """
    气泡框 - 监听"information"事件
    事件格式: text, min, max
    - min: 最小显示时间（tick数）- 在此时间内不会接受新消息的替换
    - max: 最大显示时间（tick数）- 达到此时间后自动隐藏

    气泡框的下锚点对齐到主宠物的上锚点

    新消息逻辑：
    - 如果当前未达到最小显示时间，新消息会被忽略
    - 如果达到最小显示时间，新消息直接替换文字并重置计时器
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.ArrowCursor)
        get_topmost_manager().register(self)

        # 透明度效果
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 淡入淡出动画
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._visible = False
        self._description = TOOLTIPS['bubble']

        # 事件中心
        self._event_center = get_event_center()

        # 当前气泡信息
        self._current_bubble = None

        # 待显示气泡队列：[(text, min_ticks, max_ticks, align, particle), ...]
        self._pending_queue = []
        self._fading_out = False
        self._anim_finished_handler = None

        # 字体设置
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._bug_sound = AmsBugSound()

        # 从配置文件读取气泡参数
        self._padding = BUBBLE_CONFIG.get('padding', scale_px(12))
        self._border_width = BUBBLE_CONFIG.get('border_width', scale_px(2, min_abs=1))

        # 订阅事件
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.INFORMATION, self._on_information)
        self._event_center.subscribe(EventType.LOG_ERROR, self._on_log_error)
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # UI 组件 ID
        self._ui_id = 'bubble'

        # 锚点配置：对齐到主宠物的上锚点
        self._target_ui_id = 'pet_window'
        self._target_anchor_id = 'top'
        self._self_anchor_id = 'bottom'

        # 位置偏移
        self._offset_x = 0
        self._offset_y = 0

        # 当前锚点位置
        self._anchor_point = None

        # 锚点是否可用
        self._anchor_available = False

        # 文本换行缓存（避免 paintEvent 频繁重算）
        self._line_cache_text = None
        self._line_cache_width = None
        self._line_cache_result = None

    def _wrap_text_into_lines(self, text: str, max_width: int) -> list:
        """
        将文本按宽度换行，返回行字符串列表。
        支持 \\n 硬换行，CJK/拉丁混合文本。
        """
        # 缓存命中：避免 paintEvent 频繁重建 QTextLayout
        if (self._line_cache_text == text and
                self._line_cache_width == max_width and
                self._line_cache_result is not None):
            return self._line_cache_result

        result = wrap_mixed_text(text, max_width, self._font, self._digit_font)

        self._line_cache_text = text
        self._line_cache_width = max_width
        self._line_cache_result = result
        return result

    def get_text_size(self, text: str) -> tuple:
        """
        计算文本尺寸（自动换行，宽度不超过 bubble_max_width）

        Args:
            text: 文本内容

        Returns:
            (width, height)
        """
        fm_def = QFontMetrics(self._font)
        fm_dig = QFontMetrics(self._digit_font)
        max_width = UI['bubble_max_width']
        # 内容绘制宽度 = 窗口宽度 − 左右边框各占 border_width*2 px
        content_draw_w = max_width - self._border_width * 4

        lines = self._wrap_text_into_lines(text, content_draw_w)
        n_lines = len(lines)
        line_h = max(fm_def.height(), fm_dig.height())
        content_h = n_lines * line_h

        if n_lines == 1:
            # 单行：使用实际文字宽度（不超过 max_width）
            text_w = min(
                measure_mixed_text(lines[0], self._font, self._digit_font),
                content_draw_w,
            )
            return text_w + self._padding * 2, content_h + self._padding * 2
        else:
            # 多行：宽度固定为 max_width
            return max_width, content_h + self._padding * 2

    def adjust_size_to_text(self, text: str):
        """根据文本调整窗口大小"""
        width, height = self.get_text_size(text)
        self.setFixedSize(width, height)

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

    def _on_ui_create(self, event):
        """UI ?????? - ???????"""
        target_window_id = event.data.get('window_id')
        request_anchor_id = event.data.get('anchor_id')
        requester_id = event.data.get('ui_id')

        if target_window_id == self._ui_id:
            publish_widget_anchor_response(
                self._event_center,
                self,
                window_id=self._ui_id,
                anchor_id=request_anchor_id,
                ui_id=requester_id,
            )

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 组件的锚点响应（来自 pet_window）
        # 2. pet_window 移动时的全局锚点更新（ui_id='all'）
        if ui_id == self._ui_id:
            # 专门针对此 UI 组件的锚点响应
            new_anchor_point = event.data.get('anchor_point')
            if self._anchor_point != new_anchor_point:
                self._anchor_point = new_anchor_point
                self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id:
            # pet_window 移动时的全局锚点更新
            if anchor_id == 'all':
                # pet_window 的新位置（左上角坐标）
                pet_pos = event.data.get('anchor_point')
                # 获取 pet_window 的尺寸来计算 top 锚点
                from config.config import ANIMATION
                pet_width = ANIMATION['pet_size'][0]
                pet_height = ANIMATION['pet_size'][1]
                # 计算 top 锚点位置
                new_anchor_point = QPoint(
                    pet_pos.x() + pet_width // 2,  # top 锚点的 X 坐标
                    pet_pos.y()  # top 锚点的 Y 坐标
                )
                if self._anchor_point != new_anchor_point:
                    self._anchor_point = new_anchor_point
                    self._update_position()

    def _update_position(self):
        """更新窗口位置 - 下锚点对齐到 pet_window 的上锚点"""
        if not self._anchor_point:
            return

        # self._anchor_point 是全局坐标（pet_window top 锚点的全局坐标）
        # 我们要让自己的 bottom 锚点对齐到 pet_window 的 top 锚点

        # 计算新的窗口位置
        # bottom 锚点相对于气泡框左上角的坐标是 (width // 2, height)
        # 所以气泡框的左上角应该在：(锚点.x() - width // 2, 锚点.y() - height)

        width = self.width() if self._current_bubble else scale_px(100, min_abs=1)
        height = self.height() if self._current_bubble else scale_px(40, min_abs=1)

        # X 轴：pet_window 上锚点 X 坐标 - 气泡宽度的一半 + 偏移量
        new_x = self._anchor_point.x() - width // 2 + self._offset_x

        # Y 轴：pet_window 上锚点 Y 坐标 - 气泡高度 + 偏移量
        new_y = self._anchor_point.y() - height + self._offset_y

        # 边界检查（多屏：按锚点所在屏幕裁剪）
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            width,
            height,
            point=self._anchor_point,
            fallback_widget=self,
        )

        self.move(x, y)

    def paintEvent(self, event):
        """绘制气泡框 - 参考关闭按钮的样式"""
        if not self._current_bubble:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 绘制2px黑色边框（最外层）
        painter.fillRect(self.rect(), COLORS['black'])

        # 绘制2px青色边框（中间层）
        cyan_rect = self.rect().adjusted(
            self._border_width, self._border_width,
            -self._border_width, -self._border_width
        )
        painter.fillRect(cyan_rect, COLORS['cyan'])

        # 绘制粉色背景（最内层）
        content_rect = self.rect().adjusted(
            self._border_width * 2, self._border_width * 2,
            -self._border_width * 2, -self._border_width * 2
        )
        painter.fillRect(content_rect, COLORS['pink'])

        # 根据气泡对齐方式选择水平对齐标志
        h_align = Qt.AlignLeft if self._current_bubble.align == 'left' else Qt.AlignHCenter
        # 绘制文本（逐行渲染，紧凑行距 = fm.height()，无 leading 间距）
        painter.setPen(COLORS['black'])
        painter.setFont(self._font)
        line_h = max(
            QFontMetrics(self._font).height(),
            QFontMetrics(self._digit_font).height(),
        )
        draw_lines = self._wrap_text_into_lines(self._current_bubble.text, content_rect.width())
        total_h = len(draw_lines) * line_h
        y_start = content_rect.top() + (content_rect.height() - total_h) // 2
        for idx, ln_text in enumerate(draw_lines):
            line_rect = QRect(
                content_rect.left(),
                y_start + idx * line_h,
                content_rect.width(),
                line_h,
            )
            draw_mixed_text(
                painter,
                line_rect,
                ln_text,
                self._font,
                self._digit_font,
                h_align | Qt.AlignVCenter,
            )

    def _on_information(self, event: Event):
        """处理 INFORMATION 事件 - 添加气泡到队列"""
        text      = event.data.get('text', '')
        min_ticks = event.data.get('min', 40)
        max_ticks = event.data.get('max', 100)
        align     = event.data.get('align', 'center')
        particle  = event.data.get('particle', True)  # 默认 True：替换气泡时触发上淡出粒子
        force_replace = bool(event.data.get('force_replace', False))

        if text:
            self.add_bubble(text, min_ticks, max_ticks, align, particle, force_replace)

    def _on_log_error(self, event: Event):
        """ERROR/CRITICAL 日志触发报错语音。"""
        try:
            levelno = int((event.data or {}).get('levelno', 0) or 0)
        except (TypeError, ValueError):
            levelno = 0
        if levelno >= 40:
            self._bug_sound.play()

    def _on_tick(self, event: Event):
        """Tick事件处理 - 更新气泡状态"""
        if not self._current_bubble:
            # 当前没有气泡，检查队列是否有待显示的
            self._try_show_next_in_queue()
            return

        # 如果正在淡出过程中，不处理 tick
        if self._fading_out:
            return

        # 增加已显示时间
        self._current_bubble.elapsed_ticks += 1

        # 检查是否达到最大显示时间
        if self._current_bubble.elapsed_ticks >= self._current_bubble.max_ticks:
            # 达到最大时间，隐藏气泡
            self.hide_bubble()
            return

        # 检查是否达到最小显示时间，且队列中有待显示的气泡
        if (self._current_bubble.elapsed_ticks >= self._current_bubble.min_ticks
                and self._pending_queue):
            # 有待显示的气泡，隐藏当前气泡并显示下一个
            self._show_next_bubble_from_queue()

    def add_bubble(self, text: str, min_ticks: int, max_ticks: int,
                   align: str = 'center', particle: bool = True,
                   force_replace: bool = False):
        """
        添加气泡到队列

        Args:
            text:     文本内容
            min_ticks: 最小显示时间（tick数）- 在此时间内不会接受新消息的替换
            max_ticks: 最大显示时间（tick数）- 达到此时间后自动隐藏
            align:    文本对齐方式 'left' | 'center'
            particle: True 则替换气泡时触发上淡出粒子，False 则静默替换（无粒子）
            force_replace: True 时无视当前气泡 min 直接替换（并清空待显示队列）
        """
        # 显式强制替换：无视当前显示时间。
        if force_replace:
            # 清空待显示队列，立即显示此气泡（高优先级）
            self._pending_queue.clear()
            self._replace_bubble(text, min_ticks, max_ticks, align, particle)
            return

        # 如果当前没有气泡，直接显示
        if not self._current_bubble:
            self._replace_bubble(text, min_ticks, max_ticks, align, particle)
            return

        # 检查是否达到最小显示时间
        if self._current_bubble.elapsed_ticks >= self._current_bubble.min_ticks:
            # 达到最小显示时间，可以替换
            self._replace_bubble(text, min_ticks, max_ticks, align, particle)
        else:
            # 未达到最小显示时间，将新气泡加入队列
            self._pending_queue.append((text, min_ticks, max_ticks, align, particle))

    def _show_next_bubble_from_queue(self):
        """从队列中取出下一个气泡并显示（当前气泡已达 min_ticks）"""
        if not self._pending_queue:
            return

        # 取出队列中的第一个气泡
        text, min_ticks, max_ticks, align, particle = self._pending_queue.pop(0)
        self._replace_bubble(text, min_ticks, max_ticks, align, particle)

    def _try_show_next_in_queue(self):
        """当前没有气泡时，尝试显示队列中的下一个"""
        if self._pending_queue and not self._current_bubble:
            text, min_ticks, max_ticks, align, particle = self._pending_queue.pop(0)
            self._replace_bubble(text, min_ticks, max_ticks, align, particle)

    def _replace_bubble(self, text: str, min_ticks: int, max_ticks: int,
                        align: str = 'center', particle: bool = True):
        """
        替换当前气泡的文字和计时参数

        Args:
            text:     新文本内容
            min_ticks: 最小显示时间（tick数）
            max_ticks: 最大显示时间（tick数）
            align:    文本对齐方式 'left' | 'center'
            particle: True 则在替换时对旧气泡区域触发上淡出粒子，False 则静默替换
        """
        # 若正处于淡出流程，收到新气泡时取消淡出状态，避免 tick 逻辑被永久跳过
        if self._fading_out:
            self._fading_out = False

        # 在调整尺寸前快照当前气泡区域（全局坐标），用于粒子生成
        # 仅当气泡已可见且允许粒子时触发
        if self._visible and particle:
            pre_rect = self.geometry()
            self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                'particle_id': 'up_fade',
                'area_type': 'rect',
                'area_data': (
                    pre_rect.x(),
                    pre_rect.y(),
                    pre_rect.x() + pre_rect.width(),
                    pre_rect.y() + pre_rect.height()
                )
            }))

        # 创建新的气泡信息
        self._current_bubble = BubbleInfo(text, min_ticks, max_ticks, align)

        # 调整窗口大小
        self.adjust_size_to_text(text)

        # 如果气泡未显示，显示它
        if not self._visible:
            # 先显示窗口，然后请求真实的锚点位置
            self.fade_in()
        else:
            # 如果已经显示，更新位置（文字长度可能变化）
            self._update_position()
            # 触发重绘
            self.update()

    def hide_bubble(self):
        """隐藏气泡"""
        if not self._visible:
            return

        # 如果正在淡出过程中，直接返回
        if self._fading_out:
            return

        self._visible = False
        self._anchor_available = False  # 锚点不可用
        self._fading_out = True  # 标记正在淡出

        # 在隐藏之前保存几何位置
        rect = self.geometry()

        # 发布粒子申请事件（使用保存的位置）
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        })
        self._event_center.publish(particle_event)

        # 启动淡出动画，完成后隐藏窗口并清空气泡
        # 注意：不能在动画启动前清空 _current_bubble，否则 paintEvent() 会提前返回
        self._animate(0.0, on_finished=self._on_fade_out_complete)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _on_fade_out_complete(self):
        """淡出动画完成时的回调"""
        # 淡出已被新气泡打断时，忽略旧回调
        if not self._fading_out:
            return

        # 动画完成后，清空当前气泡信息
        self._current_bubble = None

        self._fading_out = False  # 清除淡出标志
        self.hide()

        # 检查队列是否有待显示的气泡
        self._try_show_next_in_queue()

    def fade_in(self):
        """淡入显示"""
        # 如果已经可见且不在淡出，直接返回
        if self._visible:
            return

        # 新气泡到来时取消旧淡出状态
        self._fading_out = False

        # 停止任何正在进行的动画
        self._anim.stop()

        self._visible = True
        self._anchor_available = True  # 锚点可用

        # 直接计算初始锚点位置（参考 command_dialog.py 的逻辑）
        from config.config import ANIMATION
        pet_width = ANIMATION['pet_size'][0]
        pet_height = ANIMATION['pet_size'][1]

        # 获取 pet_window 的当前位置（通过事件中心查询或使用默认位置）
        # 如果有锚点响应事件队列，先等待响应；否则使用默认位置
        if self._anchor_point is None:
            # pet_window 的初始位置是屏幕中心（左上角坐标）
            # 参考 command_dialog.py 的逻辑：pet_widget.get_position() 返回的是左上角坐标
            # 所以我们需要计算 pet_window 的左上角位置，然后基于此计算 top 锚点
            screen_geom = get_screen_geometry_for_point(
                point=QCursor.pos(),
                fallback_widget=self,
            )
            pet_x = screen_geom.center().x() - pet_width // 2
            pet_y = screen_geom.center().y() - pet_height // 2
            # 计算 top 锚点位置
            self._anchor_point = QPoint(
                pet_x + pet_width // 2,  # top 锚点的 X 坐标（水平中心）
                pet_y  # top 锚点的 Y 坐标（顶部）
            )

        # 直接更新位置
        self._update_position()

        # 确保窗口已显示
        if not self.isVisible():
            self.show()

        # 发布 UI 创建请求，用于后续更新（不阻塞显示）
        create_event = Event(EventType.UI_CREATE, {
            'window_id': self._target_ui_id,
            'anchor_id': self._target_anchor_id,
            'ui_id': self._ui_id
        })
        self._event_center.publish(create_event)

        # 启动淡入动画（不需要回调）
        self._animate(1.0)

    def _animate(self, target: float, on_finished=None):
        """执行透明度动画"""
        self._anim.stop()
        if self._anim_finished_handler is not None:
            try:
                self._anim.finished.disconnect(self._anim_finished_handler)
            except (TypeError, RuntimeError):
                pass
            self._anim_finished_handler = None

        current_opacity = self._opacity.opacity()
        scaled_target = apply_ui_opacity(target)
        self._anim.setStartValue(current_opacity)
        self._anim.setEndValue(scaled_target)

        # 根据目标值设置不同的动画持续时间
        # 淡入时使用配置的 ui_fade_duration，淡出时使用 200ms
        if scaled_target > current_opacity:
            # 淡入
            self._anim.setDuration(UI['ui_fade_duration'])
        else:
            # 淡出 - 使用 200ms
            self._anim.setDuration(200)

        # 动画完成回调（在 start() 之后连接）
        if on_finished is not None:
            def on_anim_finished():
                handler = self._anim_finished_handler
                self._anim_finished_handler = None
                if handler is not None:
                    try:
                        self._anim.finished.disconnect(handler)
                    except (TypeError, RuntimeError):
                        pass
                on_finished()

            self._anim_finished_handler = on_anim_finished
            self._anim.finished.connect(on_anim_finished)

        self._anim.start()

    def clear_queue(self):
        """清空当前气泡和待显示队列"""
        self._pending_queue.clear()
        if self._visible:
            self.hide_bubble()

    def mousePressEvent(self, event):
        """鼠标点击事件 - 左键关闭，右键复制并关闭"""
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self.hide_bubble()
        elif event.button() == Qt.RightButton:
            if self._current_bubble:
                QApplication.clipboard().setText(self._current_bubble.text)
            self.hide_bubble()
        event.accept()
