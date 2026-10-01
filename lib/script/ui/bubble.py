"""气泡框类"""
from __future__ import annotations

from config.config import ANIMATION, BUBBLE_CONFIG, UI
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.core.render.visuals.application_visuals import BubbleVisualDescription
from lib.core.render.visuals.controls import (
    TICK_HIDE,
    TICK_NONE,
    TICK_REPLACE_NEXT,
    TICK_SHOW_NEXT,
    BubbleControl,
    BubbleInfo,
    PointerEvent,
)
from lib.core.render.visuals.types import Point
from lib.core.unified_draw import Layer
from lib.script.ui._particle_helper import publish_click_particle_at
from lib.script.ui.render_bridge import (
    create_control_host,
    digit_font as get_digit_font,
    qpoint_from_point,
    screen_rect_for_point as get_screen_geometry_for_point,
    pointer_position,
    text_metrics as QtTextMetrics,
    ui_font as get_ui_font,
)
from lib.script.voice.ams_bug import AmsBugSound

_logger = get_logger(__name__)


def _area_data(rect) -> tuple:
    """把核心 `Rect` 转成粒子请求要求的 `(x1, y1, x2, y2)` 整数四元组。"""
    x1, y1 = int(rect.x), int(rect.y)
    return (x1, y1, x1 + int(rect.width), y1 + int(rect.height))


class Bubble:
    """
    气泡框 - 监听"information"事件
    事件格式: text, min, max
    - min: 最小显示时间（tick数）- 在此时间内不会接受新消息的替换
    - max: 最大显示时间（tick数）- 达到此时间后自动隐藏

    气泡框的下锚点对齐到主宠物的上锚点

    新消息逻辑：
    - 如果当前未达到最小显示时间，新消息会被忽略
    - 如果达到最小显示时间，新消息直接替换文字并重置计时器

    本类不再继承 ``QWidget``：状态与绘制批次在 ``BubbleControl`` 描述对象里，
    真实窗口（窗口标志、透明度动画、绘制执行）由后端窗口宿主持有。控件自身只负责
    事件订阅、消息队列与"把描述交给宿主"。
    """

    def __init__(self):
        # 字体与间距是描述层的输入；`_font` / `_digit_font` / `_padding` /
        # `_border_width` 保留为同名属性，供像素基准测试读取它们构造的排版参数。
        self._font = get_ui_font()
        self._font.setBold(True)
        self._digit_font = get_digit_font()
        self._text_metrics = QtTextMetrics(self._font, self._digit_font)

        # 从配置文件读取气泡参数
        self._padding = BUBBLE_CONFIG.get('padding', scale_px(12))
        self._border_width = BUBBLE_CONFIG.get('border_width', scale_px(2, min_abs=1))

        self._control = BubbleControl(
            self._text_metrics,
            max_width=UI['bubble_max_width'],
            padding=self._padding,
            border_width=self._border_width,
            fade_duration_ms=UI['ui_fade_duration'],
            paint_layer=int(Layer.PET_UI),
            opacity_scale=lambda: apply_ui_opacity(1.0),
            placeholder_size=(scale_px(100, min_abs=1), scale_px(40, min_abs=1)),
            ui_id='bubble',
            target_ui_id='pet_window',
            target_anchor_id='top',
            self_anchor_id='bottom',
        )

        # 透明度效果与淡入淡出动画由后端窗口宿主持有；控件侧只保留目标值。
        self._visible = False
        self._fading_out = False
        self._description = TOOLTIPS['bubble']
        self._visual: BubbleVisualDescription | None = None
        self._bug_sound = AmsBugSound()

        self._host = create_control_host(
            paint_batch=self._paint_batch,
            on_pointer=self._on_pointer,
            on_hide_requested=lambda: self.hide_bubble(),
            on_fade_out_finished=self._on_fade_out_complete,
            layer=Layer.PET_UI,
            fade_duration_ms=UI['ui_fade_duration'],
            fade_out_duration_ms=200,
        )
        self._host._description = self._description

        # 事件中心
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.INFORMATION, self._on_information)
        self._event_center.subscribe(EventType.UI_BUBBLE_HIDE, self._on_bubble_hide)
        self._event_center.subscribe(EventType.UI_BUBBLE_REMOVE, self._on_bubble_remove)
        self._event_center.subscribe(EventType.LOG_ERROR, self._on_log_error)
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    # ==================================================================
    # 描述状态（测试与内部逻辑读取的稳定入口）
    # ==================================================================
    @property
    def _current_bubble(self):
        return self._control.current

    @_current_bubble.setter
    def _current_bubble(self, value):
        self._control.current = value

    @property
    def _pending_queue(self):
        return self._control.pending_queue

    @property
    def _anchor_point(self):
        return self._control.anchor_point

    @_anchor_point.setter
    def _anchor_point(self, value):
        self._control.set_anchor_point(value)

    @property
    def _anchor_available(self):
        return self._control.anchor_available

    @_anchor_available.setter
    def _anchor_available(self, value):
        self._control.anchor_available = bool(value)

    def _build_visual(self, text: str, align: str = "center") -> BubbleVisualDescription:
        return self._control.build_visual(text, align)

    def get_text_size(self, text: str) -> tuple:
        """计算文本尺寸（自动换行，宽度不超过 bubble_max_width）。

        Returns:
            (width, height)
        """
        return self._control.text_size(text)

    def width(self) -> int:
        return self._host.width()

    def height(self) -> int:
        return self._host.height()

    def get_anchor_point(self, anchor_id: str):
        """获取指定锚点在窗口本地坐标中的位置（整数 ``QPoint``）。"""
        return self._host.local_anchor(anchor_id)

    # ==================================================================
    # 绘制
    # ==================================================================
    def _paint_batch(self):
        if not self._current_bubble:
            return None
        visual = self._visual or self._build_visual(
            self._current_bubble.text,
            self._current_bubble.align,
        )
        return visual.batch

    def _on_pointer(self, event: PointerEvent):
        intent = self._control.click_intent(event)
        if intent.particle_id:
            publish_click_particle_at(intent.particle_id, event.screen.x, event.screen.y)
        return intent

    # ==================================================================
    # 尺寸与位置
    # ==================================================================
    def adjust_size_to_text(self, text: str):
        """根据文本调整窗口大小"""
        align = self._current_bubble.align if self._current_bubble else "center"
        self._visual = self._control.set_message(text, align)
        self._host.apply_size(self._visual.size.width, self._visual.size.height)

    def _on_ui_create(self, event):
        """UI 创建请求 - 回应自身锚点位置。"""
        target_window_id = event.data.get('window_id')
        request_anchor_id = event.data.get('anchor_id')
        requester_id = event.data.get('ui_id')

        if target_window_id == self._control.ui_id:
            self._publish_anchor_response(request_anchor_id, requester_id)

    def _publish_anchor_response(self, anchor_id: str, ui_id: str) -> None:
        event = Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': self._control.ui_id,
            'anchor_id': anchor_id,
            'anchor_point': self._control.anchor_global(
                anchor_id, self._host.geometry_rect()
            ),
            'ui_id': ui_id,
        })
        self._event_center.publish(event)

    def _on_anchor_response(self, event):
        """锚点响应事件处理"""
        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        # 处理两种情况：
        # 1. 专门针对此 UI 组件的锚点响应（来自 pet_window）
        # 2. pet_window 移动时的全局锚点更新（ui_id='all'）
        if ui_id == self._control.ui_id:
            # 专门针对此 UI 组件的锚点响应
            new_anchor_point = qpoint_from_point(event.data.get('anchor_point'))
            if new_anchor_point is None:
                return
            if self._anchor_point != new_anchor_point:
                self._control.set_anchor_point(new_anchor_point)
                self._update_position()
        elif ui_id == 'all' and window_id == self._control.target_ui_id:
            # pet_window 移动时的全局锚点更新
            if anchor_id == 'all':
                # pet_window 的新位置（左上角坐标）
                pet_pos = qpoint_from_point(event.data.get('anchor_point'))
                if pet_pos is None:
                    return
                # 获取 pet_window 的尺寸来计算 top 锚点
                pet_width = ANIMATION['pet_size'][0]
                # 计算 top 锚点位置
                new_anchor_point = Point(
                    pet_pos.x() + pet_width // 2,  # top 锚点的 X 坐标
                    pet_pos.y()  # top 锚点的 Y 坐标
                )
                if self._anchor_point != new_anchor_point:
                    self._control.set_anchor_point(new_anchor_point)
                    self._update_position()

    def _update_position(self):
        """更新窗口位置 - 下锚点对齐到 pet_window 的上锚点"""
        if not self._anchor_point:
            return

        # self._anchor_point 是全局坐标（pet_window top 锚点的全局坐标）
        # 我们要让自己的 bottom 锚点对齐到 pet_window 的 top 锚点

        screen = get_screen_geometry_for_point(
            point=self._anchor_point,
            fallback_widget=self._host,
        )
        placement = self._control.placement(screen)
        if placement is None:
            return
        self._host.move_to(placement.x, placement.y)

    # ==================================================================
    # 事件处理
    # ==================================================================
    def _on_information(self, event: Event):
        """处理 INFORMATION 事件 - 添加气泡到队列"""
        text      = event.data.get('text', '')
        min_ticks = event.data.get('min', 40)
        max_ticks = event.data.get('max', 100)
        align     = event.data.get('align', 'center')
        particle  = event.data.get('particle', True)  # 默认 True：替换气泡时触发上淡出粒子
        force_replace = bool(event.data.get('force_replace', False))
        source = event.data.get('source', '')
        task_id = event.data.get('task_id', '')
        kind = event.data.get('kind', '')

        if text:
            self.add_bubble(
                text,
                min_ticks,
                max_ticks,
                align,
                particle,
                force_replace,
                source=source,
                task_id=task_id,
                kind=kind,
            )

    def _on_bubble_remove(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self.remove_bubbles(
            source=data.get('source', ''),
            task_id=data.get('task_id', ''),
            kind=data.get('kind', ''),
        )

    def _on_bubble_hide(self, event: Event) -> None:
        del event
        self.clear_queue()

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
        action = self._control.on_tick()
        if action == TICK_NONE:
            return
        if action == TICK_SHOW_NEXT:
            self._show_next_bubble_from_queue()
        elif action == TICK_REPLACE_NEXT:
            self._show_next_bubble_from_queue()
        elif action == TICK_HIDE:
            self.hide_bubble()

    # ==================================================================
    # 队列
    # ==================================================================
    def add_bubble(self, text: str, min_ticks: int, max_ticks: int,
                   align: str = 'center', particle: bool = True,
                   force_replace: bool = False, *, source: str = '',
                   task_id: str = '', kind: str = ''):
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
        action = self._control.add(
            text, min_ticks, max_ticks, align, particle, force_replace,
            source=source, task_id=task_id, kind=kind,
        )
        if action == "replace":
            self._replace_bubble(
                text, min_ticks, max_ticks, align, particle,
                source=source, task_id=task_id, kind=kind,
            )

    def _show_next_bubble_from_queue(self):
        """从队列中取出下一个气泡并显示（当前气泡已达 min_ticks）"""
        item = self._control.pop_next()
        if item is None:
            return
        text, min_ticks, max_ticks, align, particle, source, task_id, kind = item
        self._replace_bubble(
            text, min_ticks, max_ticks, align, particle,
            source=source, task_id=task_id, kind=kind,
        )

    def _try_show_next_in_queue(self):
        """当前没有气泡时，尝试显示队列中的下一个"""
        if self._control.pending_queue and not self._control.current:
            self._show_next_bubble_from_queue()

    def _replace_bubble(self, text: str, min_ticks: int, max_ticks: int,
                        align: str = 'center', particle: bool = True, *,
                        source: str = '', task_id: str = '', kind: str = ''):
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
        if self._control.fading_out:
            self._control.fading_out = False
            self._fading_out = False

        # 在调整尺寸前快照当前气泡区域（全局坐标），用于粒子生成
        # 仅当气泡已可见且允许粒子时触发
        if self._visible and particle:
            pre_rect = self._host.geometry_rect()
            self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                'particle_id': 'up_fade',
                'area_type': 'rect',
                'area_data': _area_data(pre_rect),
            }))

        # 创建新的气泡信息
        self._control.current = BubbleInfo(
            text, min_ticks, max_ticks, align,
            source=source, task_id=task_id, kind=kind,
        )

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
            self._host.update()

    def hide_bubble(self):
        """隐藏气泡"""
        if not self._visible:
            return

        # 如果正在淡出过程中，直接返回
        if self._fading_out:
            return

        self._visible = False
        self._control.visible = False
        self._control.anchor_available = False  # 锚点不可用
        self._fading_out = True  # 标记正在淡出
        self._control.fading_out = True

        # 在隐藏之前保存几何位置
        rect = self._host.geometry_rect()

        # 发布粒子申请事件（使用保存的位置）
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': _area_data(rect),
        })
        self._event_center.publish(particle_event)

        # 启动淡出动画，完成后隐藏窗口并清空气泡
        # 注意：不能在动画启动前清空 _current_bubble，否则 paintEvent() 会提前返回
        self._host.fade_to(
            apply_ui_opacity(0.0),
            duration_ms=200,
            fade_out=True,
        )

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        enabled = bool(event.data.get('enabled', False))
        self._control.clickthrough = enabled
        self._host.set_clickthrough(enabled)

    def _on_fade_out_complete(self):
        """淡出动画完成时的回调"""
        # 淡出已被新气泡打断时，忽略旧回调
        if not self._fading_out:
            return

        # 动画完成后，清空当前气泡信息
        self._control.current = None

        self._fading_out = False  # 清除淡出标志
        self._control.fading_out = False
        self._host.hide()

        # 检查队列是否有待显示的气泡
        self._try_show_next_in_queue()

    def fade_in(self):
        """淡入显示"""
        # 如果已经可见且不在淡出，直接返回
        if self._visible:
            return

        # 新气泡到来时取消旧淡出状态
        self._fading_out = False
        self._control.fading_out = False

        self._visible = True
        self._control.visible = True
        self._control.anchor_available = True  # 锚点可用

        # 直接计算初始锚点位置（参考 command_dialog.py 的逻辑）
        pet_width = ANIMATION['pet_size'][0]
        pet_height = ANIMATION['pet_size'][1]

        # 获取 pet_window 的当前位置（通过事件中心查询或使用默认位置）
        # 如果有锚点响应事件队列，先等待响应；否则使用默认位置
        if self._anchor_point is None:
            # pet_window 的初始位置是屏幕中心（左上角坐标）
            # 参考 command_dialog.py 的逻辑：主宠核心位置是窗口左上角坐标
            # 所以我们需要计算 pet_window 的左上角位置，然后基于此计算 top 锚点
            screen_geom = get_screen_geometry_for_point(
                point=pointer_position(),
                fallback_widget=self._host,
            )
            pet_x = int(screen_geom.center.x) - pet_width // 2
            pet_y = int(screen_geom.center.y) - pet_height // 2
            # 计算 top 锚点位置
            self._control.set_anchor_point(Point(
                pet_x + pet_width // 2,  # top 锚点的 X 坐标（水平中心）
                pet_y  # top 锚点的 Y 坐标（顶部）
            ))

        # 直接更新位置
        self._update_position()

        # 确保窗口已显示
        if not self._host.isVisible():
            self._host.show()

        # 发布 UI 创建请求，用于后续更新（不阻塞显示）
        create_event = Event(EventType.UI_CREATE, {
            'window_id': self._control.target_ui_id,
            'anchor_id': self._control.target_anchor_id,
            'ui_id': self._control.ui_id
        })
        self._event_center.publish(create_event)

        # 启动淡入动画（不需要回调）
        self._animate(1.0)

    def _animate(self, target: float):
        """执行透明度动画（时长按淡入/淡出区分）。"""
        current = self._host.opacity
        scaled_target = apply_ui_opacity(target)
        fade_out = scaled_target < current
        self._host.fade_to(
            scaled_target,
            duration_ms=200 if fade_out else UI['ui_fade_duration'],
            fade_out=fade_out,
        )

    def clear_queue(self):
        """清空当前气泡和待显示队列"""
        self._control.clear_queue()
        if self._visible:
            self.hide_bubble()

    def remove_bubbles(self, *, source: str = '', task_id: str = '', kind: str = '') -> None:
        """按元数据撤销气泡，不影响其它来源的消息。"""
        matched = self._control.drop_matching(
            source=source, task_id=task_id, kind=kind,
        )
        if not matched:
            return
        if self._visible:
            self.hide_bubble()
        else:
            self._control.current = None
            self._try_show_next_in_queue()

    def isVisible(self) -> bool:
        """后端窗口当前是否可见。"""
        return bool(self._host.isVisible())

    def hide(self) -> None:
        """立即隐藏（不播淡出动画），供关机清理路径使用。"""
        self._visible = False
        self._control.visible = False
        self._control.anchor_available = False
        self._fading_out = False
        self._control.fading_out = False
        self._host.hide()

    def close(self):
        """关闭并释放后端窗口。"""
        self._host.cleanup()


__all__ = ["Bubble", "BubbleInfo"]
